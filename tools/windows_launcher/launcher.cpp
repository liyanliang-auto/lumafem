#include <windows.h>
#include <shellapi.h>
#include <string>
#include <vector>
#include <stdexcept>

namespace {
std::wstring module_path() {
    std::vector<wchar_t> buffer(512);
    for (;;) {
        const DWORD length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (!length) throw std::runtime_error("GetModuleFileNameW");
        if (length < buffer.size()) return std::wstring(buffer.data(), length);
        if (buffer.size() >= 32768) throw std::runtime_error("Module path exceeds Windows limit");
        buffer.resize(buffer.size() * 2);
    }
}

// Windows CRT argv escaping: quotes and terminal backslashes need doubling.
std::wstring quote(const std::wstring& value) {
    std::wstring result = L"\"";
    size_t slashes = 0;
    for (const wchar_t character : value) {
        if (character == L'\\') { ++slashes; continue; }
        if (character == L'"') result.append(slashes * 2 + 1, L'\\');
        else result.append(slashes, L'\\');
        slashes = 0;
        result += character;
    }
    result.append(slashes * 2, L'\\');
    return result + L'"';
}

bool regular_file(const std::wstring& path) {
    const DWORD attributes = GetFileAttributesW(path.c_str());
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}

void failure(const std::wstring& reason, const std::wstring& target, DWORD code) {
    wchar_t* system_message = nullptr;
    FormatMessageW(FORMAT_MESSAGE_ALLOCATE_BUFFER | FORMAT_MESSAGE_FROM_SYSTEM |
                   FORMAT_MESSAGE_IGNORE_INSERTS, nullptr, code, 0,
                   reinterpret_cast<wchar_t*>(&system_message), 0, nullptr);
    std::wstring text = reason + L"\n\nTarget: " + target + L"\nCode: " + std::to_wstring(code);
    if (system_message) { text += L"\n"; text += system_message; LocalFree(system_message); }
    text += L"\n\nFor diagnostics, open Windows PowerShell and run the package's "
            L"Launch-LumaFEM.ps1 with the same arguments. Check the package files and runtime.";
    MessageBoxW(nullptr, text.c_str(), L"LumaFEM could not start or finish", MB_OK | MB_ICONERROR);
}
}

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    std::wstring script;
    try {
        const auto module = module_path();
        const auto root = module.substr(0, module.find_last_of(L"\\/"));
        script = root + L"\\Launch-LumaFEM.ps1";
        for (const auto& path : {script, root + L"\\runtime\\python.exe", root + L"\\runtime\\python310.dll"}) {
            if (!regular_file(path)) {
                failure(L"A required package file is missing or inaccessible.", path, ERROR_FILE_NOT_FOUND);
                return ERROR_FILE_NOT_FOUND;
            }
        }
        std::vector<wchar_t> system(32768);
        const UINT length = GetSystemDirectoryW(system.data(), static_cast<UINT>(system.size()));
        if (!length || length >= system.size()) {
            const DWORD error = length ? ERROR_INSUFFICIENT_BUFFER : GetLastError();
            failure(L"Cannot locate the trusted Windows system directory.", script, error);
            return static_cast<int>(error);
        }
        const std::wstring powershell = std::wstring(system.data(), length) + L"\\WindowsPowerShell\\v1.0\\powershell.exe";
        if (!regular_file(powershell)) {
            failure(L"Windows PowerShell is missing or inaccessible.", powershell, ERROR_FILE_NOT_FOUND);
            return ERROR_FILE_NOT_FOUND;
        }
        int count = 0;
        wchar_t** arguments = CommandLineToArgvW(GetCommandLineW(), &count);
        if (!arguments) {
            const DWORD error = GetLastError();
            failure(L"Cannot parse the launcher arguments.", script, error);
            return static_cast<int>(error);
        }
        std::wstring command = quote(powershell) + L" -NoLogo -NoProfile -ExecutionPolicy Bypass -File " + quote(script);
        try {
            for (int index = 1; index < count; ++index) command += L" " + quote(arguments[index]);
        } catch (...) { LocalFree(arguments); throw; }
        LocalFree(arguments);
        if (command.size() >= 32767) {
            failure(L"The forwarded command line exceeds the Windows limit. Shorten the paths or arguments.", script, ERROR_BAD_LENGTH);
            return ERROR_BAD_LENGTH;
        }
        STARTUPINFOW startup{};
        startup.cb = sizeof(startup);
        PROCESS_INFORMATION child{};
        if (!CreateProcessW(powershell.c_str(), command.data(), nullptr, nullptr, FALSE,
                            CREATE_NO_WINDOW, nullptr, nullptr, &startup, &child)) {
            const DWORD error = GetLastError();
            failure(L"Cannot start Windows PowerShell.", powershell, error);
            return static_cast<int>(error);
        }
        CloseHandle(child.hThread);
        const DWORD wait = WaitForSingleObject(child.hProcess, INFINITE);
        DWORD status = 0;
        if (wait != WAIT_OBJECT_0) {
            const DWORD error = GetLastError();
            CloseHandle(child.hProcess);
            failure(L"Cannot wait for the application.", script, error);
            return static_cast<int>(error);
        }
        if (!GetExitCodeProcess(child.hProcess, &status)) {
            const DWORD error = GetLastError();
            CloseHandle(child.hProcess);
            failure(L"Cannot read the application's exit status.", script, error);
            return static_cast<int>(error);
        }
        CloseHandle(child.hProcess);
        if (status) failure(L"The application exited with a nonzero status.", script, status);
        return static_cast<int>(status);
    } catch (const std::exception&) {
        failure(L"Cannot prepare the launcher. Check package paths and available memory.", script, ERROR_GEN_FAILURE);
        return ERROR_GEN_FAILURE;
    }
}
