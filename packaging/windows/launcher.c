/*
 * DeepFind.exe — native launcher for the bundled Python runtime.
 *
 * Loads runtime\python311.dll in-process and runs app\desktop\main.py, so the running
 * process IS DeepFind.exe (own icon in the taskbar / Task Manager, no console window,
 * no stray pythonw.exe). Built with MinGW-w64:
 *   x86_64-w64-mingw32-windres launcher.rc -O coff -o launcher.res
 *   x86_64-w64-mingw32-gcc -O2 -municode -mwindows launcher.c launcher.res -o DeepFind.exe
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <shellapi.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

typedef int (*Py_Main_t)(int, wchar_t **);

static void fail(const wchar_t *what, const wchar_t *path) {
    wchar_t msg[1024];
    swprintf(msg, 1024, L"DeepFind could not start.\n\n%ls\n%ls\n\nTry reinstalling DeepFind.", what, path);
    MessageBoxW(NULL, msg, L"DeepFind", MB_OK | MB_ICONERROR);
}

/* A GUI-subsystem process has no usable stdin/stdout/stderr, and some hosts hand it
 * handles Python's startup rejects ("init_sys_streams: can't initialize sys standard
 * streams"). The app logs to files anyway, so point all three at NUL before
 * python311.dll (and its CRT) loads. */
#ifndef CONSOLE_BUILD
static void fix_std_handle(DWORD which, DWORD access) {
    SECURITY_ATTRIBUTES sa = { sizeof(sa), NULL, TRUE };
    HANDLE nul = CreateFileW(L"NUL", access, FILE_SHARE_READ | FILE_SHARE_WRITE, &sa, OPEN_EXISTING, 0, NULL);
    if (nul != INVALID_HANDLE_VALUE) SetStdHandle(which, nul);
}
#endif

int WINAPI wWinMain(HINSTANCE hInst, HINSTANCE hPrev, PWSTR cmdLine, int nShow) {
    (void)hInst; (void)hPrev; (void)cmdLine; (void)nShow;
    wchar_t dir[MAX_PATH], home[MAX_PATH], dll[MAX_PATH], script[MAX_PATH], pyw[MAX_PATH];

    DWORD n = GetModuleFileNameW(NULL, dir, MAX_PATH);
    if (n == 0 || n >= MAX_PATH) { fail(L"Install path is too long.", L""); return 1; }
    wchar_t *slash = wcsrchr(dir, L'\\');
    if (slash) *slash = 0;

    swprintf(home, MAX_PATH, L"%ls\\runtime", dir);
    swprintf(dll, MAX_PATH, L"%ls\\runtime\\python311.dll", dir);
    swprintf(pyw, MAX_PATH, L"%ls\\runtime\\pythonw.exe", dir);
    swprintf(script, MAX_PATH, L"%ls\\app\\desktop\\main.py", dir);

    if (GetFileAttributesW(script) == INVALID_FILE_ATTRIBUTES) { fail(L"Missing application files:", script); return 1; }

#ifndef CONSOLE_BUILD
    fix_std_handle(STD_INPUT_HANDLE, GENERIC_READ);
    fix_std_handle(STD_OUTPUT_HANDLE, GENERIC_WRITE);
    fix_std_handle(STD_ERROR_HANDLE, GENERIC_WRITE);
    SetEnvironmentVariableW(L"DEEPFIND_LAUNCHER", L"1"); /* tells main.py to log stdout/stderr to a file */
#endif

    /* Isolated runtime: ignore any Python the user has installed. */
    SetEnvironmentVariableW(L"PYTHONHOME", home);
    SetEnvironmentVariableW(L"PYTHONPATH", NULL);
    SetEnvironmentVariableW(L"PYTHONSTARTUP", NULL);
    SetEnvironmentVariableW(L"PYTHONNOUSERSITE", L"1");
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    SetEnvironmentVariableW(L"DEEPFIND_PYTHON", pyw);   /* used to spawn the model downloader */
    SetDllDirectoryW(home);

    HMODULE py = LoadLibraryExW(dll, NULL, LOAD_WITH_ALTERED_SEARCH_PATH);
    if (!py) { fail(L"Could not load the Python runtime:", dll); return 1; }
    Py_Main_t Py_Main = (Py_Main_t)GetProcAddress(py, "Py_Main");
    if (!Py_Main) { fail(L"The Python runtime is damaged:", dll); return 1; }

    int argc = 0;
    wchar_t **argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    wchar_t **pargv = (wchar_t **)calloc((size_t)argc + 4, sizeof(wchar_t *));
    int k = 0;
    pargv[k++] = argv ? argv[0] : L"DeepFind";
    pargv[k++] = L"-s";            /* no user site-packages */
    pargv[k++] = script;
    for (int i = 1; argv && i < argc; i++) pargv[k++] = argv[i];   /* e.g. --debug, --headless */
    pargv[k] = NULL;

    return Py_Main(k, pargv);
}

#ifdef CONSOLE_BUILD
int wmain(void) { return wWinMain(GetModuleHandleW(NULL), NULL, GetCommandLineW(), SW_SHOWNORMAL); }
#endif
