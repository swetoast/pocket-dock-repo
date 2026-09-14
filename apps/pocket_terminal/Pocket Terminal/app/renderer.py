import ctypes
import ctypes.util
from dataclasses import dataclass

SDL_INIT_VIDEO = 0x00000020
SDL_INIT_JOYSTICK = 0x00000200
SDL_WINDOW_FULLSCREEN = 0x00000001
SDL_WINDOWPOS_UNDEFINED = 0x1FFF0000
SDL_RENDERER_SOFTWARE = 0x00000001
SDL_RENDERER_ACCELERATED = 0x00000002
SDL_RENDERER_PRESENTVSYNC = 0x00000004
SDL_PIXELFORMAT_RGBA32 = 376840196
SDL_TEXTUREACCESS_STREAMING = 1
SDL_QUIT = 0x100
SDL_JOYHATMOTION = 0x602
SDL_JOYBUTTONDOWN = 0x603
SDL_JOYBUTTONUP = 0x604


class SDL_Event(ctypes.Union):
    _fields_ = [("type", ctypes.c_uint32), ("data", ctypes.c_uint8 * 56)]


@dataclass(frozen=True)
class Event:
    kind: str
    number: int = 0
    value: int = 0


class Renderer:
    width = 640
    height = 480

    def __init__(self):
        self.sdl = self._load_sdl()
        self._declare_api()
        self.window = None
        self.renderer = None
        self.texture = None
        self.joystick = None
        self.event = SDL_Event()
        self.buffer = None

        if self.sdl.SDL_Init(SDL_INIT_VIDEO | SDL_INIT_JOYSTICK) != 0:
            raise RuntimeError(self.error())
        try:
            self.window = self.sdl.SDL_CreateWindow(
                b"Pocket Terminal",
                SDL_WINDOWPOS_UNDEFINED,
                SDL_WINDOWPOS_UNDEFINED,
                self.width,
                self.height,
                SDL_WINDOW_FULLSCREEN,
            )
            if not self.window:
                raise RuntimeError(self.error())

            flags = SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC
            self.renderer = self.sdl.SDL_CreateRenderer(self.window, -1, flags)
            if not self.renderer:
                self.renderer = self.sdl.SDL_CreateRenderer(
                    self.window, -1, SDL_RENDERER_SOFTWARE
                )
            if not self.renderer:
                raise RuntimeError(self.error())

            self.texture = self.sdl.SDL_CreateTexture(
                self.renderer,
                SDL_PIXELFORMAT_RGBA32,
                SDL_TEXTUREACCESS_STREAMING,
                self.width,
                self.height,
            )
            if not self.texture:
                raise RuntimeError(self.error())

            if self.sdl.SDL_NumJoysticks() > 0:
                self.joystick = self.sdl.SDL_JoystickOpen(0)
        except Exception:
            self.close()
            raise

    @staticmethod
    def _load_sdl():
        candidates = (
            ctypes.util.find_library("SDL2"),
            "libSDL2-2.0.so.0",
            "/usr/lib/aarch64-linux-gnu/libSDL2-2.0.so.0",
        )
        for candidate in filter(None, candidates):
            try:
                return ctypes.CDLL(candidate)
            except OSError:
                continue
        raise RuntimeError("SDL2 library not found")

    def _declare_api(self):
        sdl = self.sdl
        sdl.SDL_Init.argtypes = [ctypes.c_uint32]
        sdl.SDL_Init.restype = ctypes.c_int
        sdl.SDL_GetError.argtypes = []
        sdl.SDL_GetError.restype = ctypes.c_char_p
        sdl.SDL_CreateWindow.argtypes = [
            ctypes.c_char_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_int, ctypes.c_int, ctypes.c_uint32,
        ]
        sdl.SDL_CreateWindow.restype = ctypes.c_void_p
        sdl.SDL_CreateRenderer.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_uint32]
        sdl.SDL_CreateRenderer.restype = ctypes.c_void_p
        sdl.SDL_CreateTexture.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_int,
            ctypes.c_int, ctypes.c_int,
        ]
        sdl.SDL_CreateTexture.restype = ctypes.c_void_p
        sdl.SDL_UpdateTexture.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
        ]
        sdl.SDL_UpdateTexture.restype = ctypes.c_int
        sdl.SDL_RenderClear.argtypes = [ctypes.c_void_p]
        sdl.SDL_RenderClear.restype = ctypes.c_int
        sdl.SDL_RenderCopy.argtypes = [
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
        ]
        sdl.SDL_RenderCopy.restype = ctypes.c_int
        sdl.SDL_RenderPresent.argtypes = [ctypes.c_void_p]
        sdl.SDL_RenderPresent.restype = None
        sdl.SDL_PollEvent.argtypes = [ctypes.POINTER(SDL_Event)]
        sdl.SDL_PollEvent.restype = ctypes.c_int
        sdl.SDL_NumJoysticks.argtypes = []
        sdl.SDL_NumJoysticks.restype = ctypes.c_int
        sdl.SDL_JoystickOpen.argtypes = [ctypes.c_int]
        sdl.SDL_JoystickOpen.restype = ctypes.c_void_p
        sdl.SDL_JoystickClose.argtypes = [ctypes.c_void_p]
        sdl.SDL_JoystickClose.restype = None
        sdl.SDL_DestroyTexture.argtypes = [ctypes.c_void_p]
        sdl.SDL_DestroyTexture.restype = None
        sdl.SDL_DestroyRenderer.argtypes = [ctypes.c_void_p]
        sdl.SDL_DestroyRenderer.restype = None
        sdl.SDL_DestroyWindow.argtypes = [ctypes.c_void_p]
        sdl.SDL_DestroyWindow.restype = None
        sdl.SDL_Quit.argtypes = []
        sdl.SDL_Quit.restype = None

    def error(self):
        return (self.sdl.SDL_GetError() or b"SDL error").decode("utf-8", "replace")

    def poll(self):
        events = []
        while self.sdl.SDL_PollEvent(ctypes.byref(self.event)):
            raw = bytes(self.event.data)
            event_type = self.event.type
            if event_type == SDL_QUIT:
                events.append(Event("quit"))
            elif event_type == SDL_JOYHATMOTION:
                events.append(Event("hat", raw[12], raw[13]))
            elif event_type == SDL_JOYBUTTONDOWN:
                events.append(Event("down", raw[12], 1))
            elif event_type == SDL_JOYBUTTONUP:
                events.append(Event("up", raw[12], 0))
        return events

    def present(self, image):
        rgba = image.convert("RGBA")
        if rgba.size != (self.width, self.height):
            raise ValueError("frame must be exactly 640 x 480")
        self.buffer = ctypes.create_string_buffer(rgba.tobytes("raw", "RGBA"))
        if self.sdl.SDL_UpdateTexture(self.texture, None, self.buffer, self.width * 4) != 0:
            raise RuntimeError(self.error())
        if self.sdl.SDL_RenderClear(self.renderer) != 0:
            raise RuntimeError(self.error())
        if self.sdl.SDL_RenderCopy(self.renderer, self.texture, None, None) != 0:
            raise RuntimeError(self.error())
        self.sdl.SDL_RenderPresent(self.renderer)

    def close(self):
        if self.joystick:
            self.sdl.SDL_JoystickClose(self.joystick)
            self.joystick = None
        if self.texture:
            self.sdl.SDL_DestroyTexture(self.texture)
            self.texture = None
        if self.renderer:
            self.sdl.SDL_DestroyRenderer(self.renderer)
            self.renderer = None
        if self.window:
            self.sdl.SDL_DestroyWindow(self.window)
            self.window = None
        if getattr(self, "sdl", None):
            self.sdl.SDL_Quit()
