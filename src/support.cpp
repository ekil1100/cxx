#include "support.hpp"
#include "picosha2.h"

#include <algorithm>
#include <array>
#include <cerrno>
#include <cctype>
#include <csignal>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iostream>
#include <iterator>
#include <string_view>
#include <sys/file.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char** environ;

namespace cxx::detail {
namespace {
volatile std::sig_atomic_t child_group = 0;
volatile std::sig_atomic_t interrupted = 0;
void forward_signal(int signal) {
    const int saved_errno = errno;
    interrupted = signal;
    if (child_group > 0) ::kill(-child_group, signal);
    errno = saved_errno;
}

template<std::size_t N>
[[noreturn]] void child_failure(const char (&message)[N], int status) {
    // No allocation, streams or strerror between fork and exec.
    std::size_t offset = 0;
    while (offset < N - 1) {
        const auto count = write(STDERR_FILENO, message + offset, N - 1 - offset);
        if (count < 0 && errno == EINTR) continue;
        if (count <= 0) break;
        offset += static_cast<std::size_t>(count);
    }
    _exit(status);
}

int set_foreground_group(int fd, pid_t group) {
    // The waiting parent is in the background when it reclaims the terminal.
    sigset_t blocked, previous;
    sigemptyset(&blocked);
    sigaddset(&blocked, SIGTTOU);
    if (sigprocmask(SIG_BLOCK, &blocked, &previous) < 0) return -1;
    int result;
    do { result = tcsetpgrp(fd, group); } while (result < 0 && errno == EINTR);
    const int saved_errno = errno;
    sigprocmask(SIG_SETMASK, &previous, nullptr);
    errno = saved_errno;
    return result;
}

struct Temporary {
    fs::path path;
    ~Temporary() { std::error_code ec; fs::remove(path, ec); }
};

struct FileDescriptor {
    int fd;
    ~FileDescriptor() { if (fd >= 0) close(fd); }
};

fs::path temporary_for(const fs::path& path) {
    return path.string() + ".tmp." + std::to_string(::getpid());
}

bool is_current(const fs::path& output, const std::string& key) {
    if (!fs::is_regular_file(output) || !fs::is_regular_file(output.string() + ".key")) return false;
    return read_file(output.string() + ".key") == key + "\n" + hash_file(output) + "\n";
}

void publish(const fs::path& temporary, const fs::path& output, const std::string& key) {
    if (!fs::is_regular_file(fs::symlink_status(temporary))) {
        throw Error("Action did not produce a regular output file: " + output.string());
    }
    const auto digest = hash_file(temporary);
    // Publish metadata first. Until the final rename, its output digest will not
    // match the old artifact, so an interrupted transaction is a safe cache miss.
    write_atomic(output.string() + ".key", key + "\n" + digest + "\n");
    fs::rename(temporary, output);
}

std::vector<fs::path> dependency_paths(const fs::path& depfile, const fs::path& cwd) {
    const auto text = read_file(depfile);
    const auto colon = text.find(':'); // The target is fixed to "cxxdeps".
    if (colon == std::string::npos) throw Error("Invalid compiler dependency file");
    std::vector<fs::path> paths;
    std::string token;
    auto flush = [&] {
        // A lexical '..' collapse can select a different file after a symlink.
        if (!token.empty()) { paths.push_back(cwd / token); token.clear(); }
    };
    for (std::size_t i = colon + 1; i < text.size(); ++i) {
        const char c = text[i];
        if (c == '\\' && i + 1 < text.size()) {
            const char next = text[++i];
            if (next == '\n') { flush(); continue; }
            if (next == '\r' && i + 1 < text.size() && text[i + 1] == '\n') { ++i; flush(); continue; }
            token += next;
        } else if (c == '$' && i + 1 < text.size() && text[i + 1] == '$') {
            token += '$'; ++i;
        } else if (std::isspace(static_cast<unsigned char>(c))) flush();
        else token += c;
    }
    flush();
    if (paths.empty()) throw Error("Compiler reported no source dependencies");
    return paths;
}

// Clang replaces literal POSIX backslashes with '/' in dependency paths.
// A normalized name can even select an existing decoy. Do not try to reverse
// that mapping; these actions must compile the original input every time.
std::string lossy_dependency_path(const fs::path& source, const fs::path& cwd,
                                  const std::vector<std::string>& flags) {
    if (cwd.string().contains('\\')) return "working directory";
    if (source.string().contains('\\')) return "source path";
    constexpr std::string_view path_options[] = {
        "-I", "-isystem", "-iquote", "-idirafter", "-isysroot", "--sysroot",
        "-F", "-iframework", "-include", "-imacros"
    };
    for (std::size_t i = 0; i < flags.size(); ++i) {
        const auto& flag = flags[i];
        // Definitions contain C++ escapes, not filesystem paths.
        if (flag == "-D" || flag == "-U") { ++i; continue; }
        for (const auto option : path_options) {
            if (flag == option) {
                if (i + 1 < flags.size() && flags[++i].contains('\\')) return std::string(option) + " path";
                break;
            }
            if (flag.starts_with(option) && flag.contains('\\')) return std::string(option) + " path";
        }
    }
    for (const auto* name : {"CPATH", "CPLUS_INCLUDE_PATH", "C_INCLUDE_PATH",
                             "OBJC_INCLUDE_PATH", "OBJCPLUS_INCLUDE_PATH", "SDKROOT",
                             "DEVELOPER_DIR", "GCC_EXEC_PREFIX"}) {
        const char* value = std::getenv(name);
        if (value && std::strchr(value, '\\')) return std::string(name) + " environment path";
    }
    return {};
}

// Unlike depfiles, Clang's preprocessor line markers preserve the actual header
// spelling, including macro includes and transitive headers. Parse their C string
// escapes: an escaped quote is not a path backslash. Control bytes can split
// depfile paths. Unknown encodings are unsafe too; never guess which raw
// dependency a lossy spelling was meant to identify.
bool has_lossy_line_marker(const std::string& text) {
    std::string_view remaining(text);
    while (!remaining.empty()) {
        const auto end = remaining.find('\n');
        const auto line = remaining.substr(0, end);
        remaining.remove_prefix(end == std::string_view::npos ? remaining.size() : end + 1);
        if (!line.starts_with("# ")) continue;
        std::size_t i = 2;
        while (i < line.size() && line[i] == ' ') ++i;
        const auto number = i;
        while (i < line.size() && line[i] >= '0' && line[i] <= '9') ++i;
        if (i == number) continue; // Other preprocessor directives are not markers.
        while (i < line.size() && line[i] == ' ') ++i;
        if (i == line.size() || line[i++] != '"') return true;
        bool closed = false;
        while (i < line.size()) {
            const auto c = static_cast<unsigned char>(line[i++]);
            if (c <= 0x1f || c == 0x7f) return true;
            if (c == '"') { closed = true; break; }
            if (c != '\\') continue;
            if (i == line.size()) return true;
            const char escaped = line[i++];
            if (escaped == '\\' || std::string_view("abfnrtv").contains(escaped)) return true;
            if (escaped == '"' || escaped == '\'' || escaped == '?') continue;
            if (escaped >= '0' && escaped <= '7') {
                unsigned value = escaped - '0';
                for (int digits = 1; digits < 3 && i < line.size() &&
                     line[i] >= '0' && line[i] <= '7'; ++digits) {
                    value = value * 8 + (line[i++] - '0');
                }
                if (value <= 0x1f || value == 0x7f || value == '\\' || value > 255) return true;
                continue;
            }
            return true; // An unsupported escape makes the filename ambiguous.
        }
        if (!closed) return true;
    }
    return false;
}

// Symbol aliases and a single .globl declaration do not read assembler files.
// libstdc++ <iostream> uses .globl for its initialization symbol.
// Other assembly can use .incbin/.include or assembler macros, which Clang's
// C++ dependency file does not cover. Recompile those actions on every build.
bool has_untracked_assembly(const std::string& text) {
    auto word = [](char c) { return std::isalnum(static_cast<unsigned char>(c)) || c == '_'; };
    auto whitespace = [&](std::size_t& i) {
        while (i < text.size() && std::isspace(static_cast<unsigned char>(text[i]))) ++i;
    };
    for (std::size_t i = 0; i < text.size();) {
        if (text[i] == '"' || text[i] == '\'') {
            const char delimiter = text[i++];
            while (i < text.size()) {
                if (text[i] == '\\') { i += std::min<std::size_t>(2, text.size() - i); continue; }
                if (text[i++] == delimiter) break;
            }
        } else if (std::isdigit(static_cast<unsigned char>(text[i]))) {
            // Apostrophes inside numeric literals are separators, not quotes.
            while (i < text.size() && (word(text[i]) || text[i] == '.' || text[i] == '\'')) ++i;
        } else if (word(text[i])) {
            const auto start = i;
            while (i < text.size() && word(text[i])) ++i;
            const auto token = text.substr(start, i - start);
            if ((token == "R" || token == "u8R" || token == "uR" || token == "UR" || token == "LR") &&
                i < text.size() && text[i] == '"') {
                const auto opening = text.find('(', i + 1);
                if (opening == std::string::npos || opening - i > 17) return true;
                const auto ending = ")" + text.substr(i + 1, opening - i - 1) + "\"";
                const auto close = text.find(ending, opening + 1);
                if (close == std::string::npos) return true;
                i = close + ending.size();
                continue;
            }
            if (token != "asm" && token != "__asm" && token != "__asm__") continue;
            auto next = i;
            whitespace(next);
            if (next == text.size() || text[next++] != '(') return true;
            whitespace(next);
            bool saw_literal = false;
            std::string assembly;
            while (next < text.size() && text[next] == '"') {
                saw_literal = true;
                const auto literal = ++next;
                while (next < text.size() && (word(text[next]) || text[next] == '$' ||
                                             text[next] == '.' || text[next] == ' ')) ++next;
                assembly.append(text, literal, next - literal);
                // Escapes, newlines and separators remain conservatively uncached.
                if (next == text.size() || text[next++] != '"') return true;
                whitespace(next);
            }
            if (!saw_literal || next == text.size() || text[next] != ')') return true;
            std::string_view symbol = assembly;
            if (symbol.starts_with(".globl ")) {
                symbol.remove_prefix(7);
                if (symbol.empty()) return true;
            }
            if (!std::ranges::all_of(symbol, [&](char c) { return word(c) || c == '$'; })) return true;
            i = next + 1;
        } else ++i;
    }
    return false;
}

void require_success(const std::vector<std::string>& args, const fs::path& cwd, bool verbose) {
    const auto code = run_process(args, cwd, verbose);
    if (code != 0) throw Error("Command failed (exit " + std::to_string(code) + "): " + args.front());
}
}

std::string read_file(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw Error("Cannot read file: " + path.string());
    std::string result{std::istreambuf_iterator<char>(input), {}};
    if (input.bad()) throw Error("Failed reading file: " + path.string());
    return result;
}

void write_atomic(const fs::path& path, const std::string& contents) {
    if (!path.parent_path().empty()) fs::create_directories(path.parent_path());
    Temporary temp{temporary_for(path)};
    {
        std::ofstream output(temp.path, std::ios::binary | std::ios::trunc);
        if (!output) throw Error("Cannot write file: " + temp.path.string());
        output.write(contents.data(), static_cast<std::streamsize>(contents.size()));
        output.close();
        if (!output) throw Error("Failed writing file: " + temp.path.string());
    }
    fs::rename(temp.path, path);
}

std::string hash_file(const fs::path& path) {
    std::ifstream input(path, std::ios::binary);
    if (!input) throw Error("Cannot hash file: " + path.string());
    picosha2::hash256_one_by_one hash;
    std::array<char, 64 * 1024> buffer{};
    while (input) {
        input.read(buffer.data(), buffer.size());
        hash.process(buffer.begin(), buffer.begin() + input.gcount());
    }
    if (input.bad()) throw Error("Failed hashing file: " + path.string());
    hash.finish();
    return picosha2::get_hash_hex_string(hash);
}

std::string fingerprint(const std::vector<std::string>& fields) {
    picosha2::hash256_one_by_one hash;
    for (const auto& field : fields) {
        const auto length = std::to_string(field.size()) + ":";
        hash.process(length.begin(), length.end());
        hash.process(field.begin(), field.end());
    }
    hash.finish();
    return picosha2::get_hash_hex_string(hash);
}

std::string environment_fingerprint() {
    std::vector<std::string> entries;
    for (char** entry = environ; entry && *entry; ++entry) entries.emplace_back(*entry);
    std::ranges::sort(entries);
    return fingerprint(entries);
}

fs::path resolve_program(const std::string& name) {
    if (name.empty()) throw Error("Empty tool path");
    auto usable = [](const fs::path& path) {
        return fs::is_regular_file(path) && ::access(path.c_str(), X_OK) == 0;
    };
    if (name.contains('/')) {
        const auto path = fs::absolute(name);
        if (!usable(path)) throw Error("Tool is not executable: " + path.string());
        // Preserve the invoked filename: compiler symlinks can select driver mode.
        return path;
    }
    const char* env = std::getenv("PATH");
    std::string paths = env ? env : "";
    std::size_t start = 0;
    do {
        const auto end = paths.find(':', start);
        const auto directory = paths.substr(start, end == std::string::npos ? end : end - start);
        const auto candidate = fs::absolute(fs::path(directory.empty() ? "." : directory) / name);
        if (usable(candidate)) return candidate;
        if (end == std::string::npos) break;
        start = end + 1;
    } while (true);
    throw Error("Tool not found in PATH: " + name);
}

std::string quote_cpp(const std::string& value) {
    std::string result = "\"";
    for (unsigned char c : value) {
        if (c == '\\' || c == '"') { result += '\\'; result += static_cast<char>(c); }
        else if (c < 32 || c >= 127) {
            result += '\\';
            result += static_cast<char>('0' + ((c >> 6) & 7));
            result += static_cast<char>('0' + ((c >> 3) & 7));
            result += static_cast<char>('0' + (c & 7));
        } else result += static_cast<char>(c);
    }
    return result + '"';
}

int run_process(const std::vector<std::string>& args, const fs::path& cwd, bool verbose) {
    if (args.empty()) throw Error("Empty command");
    std::vector<char*> argv;
    for (const auto& arg : args) argv.push_back(const_cast<char*>(arg.c_str()));
    argv.push_back(nullptr);
    if (verbose) {
        std::cerr << "[command]";
        for (const auto& arg : args) std::cerr << ' ' << quote_cpp(arg);
        std::cerr << '\n';
    }
    std::cout.flush();
    std::cerr.flush();
    // Prepare all C++ data before fork. The child only calls async-signal-safe
    // POSIX functions, including on failures before exec.
    char* const* child_argv = argv.data();
    const char* executable = child_argv[0];
    const char* directory = cwd.c_str();
    // stdin may be redirected or closed while the application uses /dev/tty.
    // Own a separate descriptor until all wait/restore paths finish. The child
    // closes its copy before any possible exit; CLOEXEC is a second safeguard.
    FileDescriptor terminal{-1};
    do { terminal.fd = open("/dev/tty", O_RDWR | O_CLOEXEC | O_NOCTTY); }
    while (terminal.fd < 0 && errno == EINTR);
    if (terminal.fd < 0 && errno != ENXIO && errno != ENODEV && errno != ENOENT) {
        throw Error("Cannot open controlling terminal: " + std::string(std::strerror(errno)));
    }
    const int terminal_fd = terminal.fd;
    const pid_t parent_group = getpgrp();
    const bool foreground = terminal_fd >= 0 && tcgetpgrp(terminal_fd) == parent_group;
    int gate[2] = {-1, -1};
    if (pipe(gate) < 0) {
        throw Error("Cannot create process setup pipe: " + std::string(std::strerror(errno)));
    }
    auto close_gate = [&] {
        if (gate[0] >= 0) close(gate[0]);
        if (gate[1] >= 0) close(gate[1]);
    };

    constexpr int signals[] = {SIGINT, SIGTERM, SIGHUP, SIGQUIT};
    struct sigaction action{}, previous[4]{};
    sigset_t blocked, previous_mask;
    sigemptyset(&blocked);
    for (int signal : signals) sigaddset(&blocked, signal);
    if (sigprocmask(SIG_BLOCK, &blocked, &previous_mask) < 0) {
        const int error = errno;
        close_gate();
        throw Error("Cannot block process signals: " + std::string(std::strerror(error)));
    }
    action.sa_handler = forward_signal;
    action.sa_mask = blocked;
    action.sa_flags = SA_RESTART;
    interrupted = 0;
    child_group = 0;
    int installed = 0;
    auto restore_signals = [&] {
        for (int i = 0; i < installed; ++i) sigaction(signals[i], &previous[i], nullptr);
        sigprocmask(SIG_SETMASK, &previous_mask, nullptr);
    };
    for (int signal : signals) {
        if (sigaction(signal, &action, &previous[installed]) < 0) {
            const int error = errno;
            close_gate();
            restore_signals();
            throw Error("Cannot install process signal handler: " + std::string(std::strerror(error)));
        }
        ++installed;
    }
    const pid_t child = fork();
    if (child == 0) {
        if (terminal_fd >= 0) close(terminal_fd);
        close(gate[1]);
        for (int i = 0; i < installed; ++i) {
            if (sigaction(signals[i], &previous[i], nullptr) < 0) {
                child_failure("cxx: child signal handler restore failed\n", 126);
            }
        }
        char byte;
        ssize_t count;
        do { count = read(gate[0], &byte, 1); } while (count < 0 && errno == EINTR);
        close(gate[0]);
        if (count != 0) child_failure("cxx: child process setup gate failed\n", 126);
        if (sigprocmask(SIG_SETMASK, &previous_mask, nullptr) < 0) {
            child_failure("cxx: child signal mask restore failed\n", 126);
        }
        if (chdir(directory) != 0) {
            child_failure("cxx: cannot enter command working directory\n", 126);
        }
        execv(executable, child_argv);
        child_failure("cxx: cannot execute command\n", 127);
    }
    if (child < 0) {
        const int error = errno;
        close_gate();
        restore_signals();
        throw Error("Cannot fork: " + std::string(std::strerror(error)));
    }
    const char* failure = nullptr;
    int error = 0;
    // Only the parent sets the group. Concurrent parent/child setpgid calls can
    // fail with EPERM on macOS, even when both request the same group. Every
    // child waits at the gate, including without a controlling terminal, so it
    // cannot exec or change session before this checked call and the handoff.
    if (setpgid(child, child) < 0) {
        failure = "Cannot create command process group";
        error = errno;
    }
    child_group = child;
    bool handed_off = false;
    // Recheck ownership: a background invocation must never steal the terminal.
    if (!failure && foreground && tcgetpgrp(terminal_fd) == parent_group) {
        if (set_foreground_group(terminal_fd, child) < 0) {
            failure = "Cannot give terminal to command";
            error = errno;
        } else handed_off = true;
    }
    if (failure) {
        kill(-child, SIGKILL);
        kill(child, SIGKILL);
    }
    // EOF releases the child only after handoff (or after killing it on error).
    // No write means an early child exit cannot cause SIGPIPE in the parent.
    close_gate();
    sigprocmask(SIG_SETMASK, &previous_mask, nullptr);
    int status = 0;
    pid_t waited;
    do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
    if (waited < 0) {
        failure = "Cannot wait for child process";
        error = errno;
        kill(-child, SIGKILL);
        kill(child, SIGKILL);
        while (waitpid(child, &status, 0) < 0 && errno == EINTR) {}
    }
    if (handed_off && set_foreground_group(terminal_fd, parent_group) < 0 && !failure) {
        failure = "Cannot restore terminal foreground group";
        error = errno;
    }
    sigprocmask(SIG_BLOCK, &blocked, nullptr);
    const int received_signal = interrupted;
    child_group = 0;
    restore_signals();
    if (failure) throw Error(std::string(failure) + ": " + std::strerror(error));
    if (received_signal) return 128 + received_signal;
    if (WIFEXITED(status)) return WEXITSTATUS(status);
    return WIFSIGNALED(status) ? 128 + WTERMSIG(status) : 1;
}

void validate_install_path(const fs::path& path, bool directory) {
    fs::path current;
    for (auto it = path.begin(); it != path.end(); ++it) {
        current /= *it;
        const auto status = fs::symlink_status(current);
        const bool parent = std::next(it) != path.end() || directory;
        if (fs::is_symlink(status)) throw Error("Install path contains a symlink: " + current.string());
        if (fs::exists(status) && (parent ? !fs::is_directory(status) : !fs::is_regular_file(status)))
            throw Error(std::string("Install path must be a ") + (parent ? "directory: " : "regular file: ") + current.string());
    }
}

void install_file_atomic(const fs::path& source, const fs::path& destination) {
    validate_install_path(destination);
    auto identity = [&] {
        struct stat status{};
        if (::stat(source.c_str(), &status) != 0 || !S_ISREG(status.st_mode))
            throw Error("Install source must be a regular file: " + source.string());
        return fingerprint({fs::canonical(source).string(), std::to_string(status.st_dev),
                            std::to_string(status.st_ino), std::to_string(status.st_mode),
                            std::to_string(status.st_size), hash_file(source)});
    };
    const auto before = identity();
    const auto permissions = fs::status(source).permissions();
    const auto digest = hash_file(source);
    if (fs::exists(destination) && fs::equivalent(source, destination))
        throw Error("Install destination overlaps source: " + destination.string());
    fs::create_directories(destination.parent_path());
    validate_install_path(destination);
    // A unique file in the destination directory keeps rename on one filesystem
    // and avoids overwriting unrelated files with predictable staging names.
    auto pattern = (destination.parent_path() / ".cxx-install-XXXXXX").string();
    FileDescriptor staging{::mkstemp(pattern.data())};
    if (staging.fd < 0) throw Error("Cannot stage install file: " + destination.string());
    Temporary temp{pattern};
    fs::copy_file(source, temp.path, fs::copy_options::overwrite_existing);
    fs::permissions(temp.path, permissions);
    if (identity() != before || hash_file(temp.path) != digest)
        throw Error("Install source changed during copy; retry: " + source.string());
    validate_install_path(destination);
    if (fs::exists(destination) && fs::equivalent(source, destination))
        throw Error("Install destination overlaps source: " + destination.string());
    fs::rename(temp.path, destination);
    std::cerr << "[install] " << destination.string() << '\n';
}

ProjectLock::ProjectLock(const fs::path& path) {
    fs::create_directories(path.parent_path());
    fd_ = open(path.c_str(), O_CREAT | O_RDWR | O_CLOEXEC, 0600);
    if (fd_ < 0) throw Error("Cannot open project lock: " + path.string());
    if (flock(fd_, LOCK_EX | LOCK_NB) != 0) {
        close(fd_); fd_ = -1;
        throw Error("Another cxx command holds this project's build lock");
    }
}
ProjectLock::~ProjectLock() { if (fd_ >= 0) close(fd_); }

void write_dependency_scan(const fs::path& path, const DependencyScan& scan) {
    std::string contents(1, scan.complete ? '1' : '0');
    for (const auto& input : scan.paths) {
        contents += input.string();
        contents += '\0';
    }
    write_atomic(path, contents);
}

DependencyScan read_dependency_scan(const fs::path& path) {
    const auto contents = read_file(path);
    if (contents.empty() || (contents.front() != '0' && contents.front() != '1'))
        throw Error("Invalid bootstrap dependency scan: " + path.string());
    DependencyScan scan;
    scan.complete = contents.front() == '1';
    for (std::size_t start = 1; start < contents.size();) {
        const auto end = contents.find('\0', start);
        if (end == std::string::npos || end == start)
            throw Error("Invalid bootstrap dependency scan: " + path.string());
        scan.paths.emplace_back(contents.substr(start, end - start));
        start = end + 1;
    }
    return scan;
}

bool compile_cpp(const fs::path& compiler, const fs::path& source,
                 const fs::path& output, const std::vector<std::string>& flags,
                 const std::string& tool_identity, const fs::path& cwd,
                 bool verbose, const std::string& label, DependencyScan* discovered_inputs) {
    fs::create_directories(output.parent_path());
    Temporary scan{output.string() + ".scan.ii"};
    Temporary depfile{output.string() + ".scan.d"};
    std::string lossy_path;
    bool untracked = false;
    std::vector<std::string> preprocess{compiler.string()};
    preprocess.insert(preprocess.end(), flags.begin(), flags.end());
    preprocess.insert(preprocess.end(), {"-x", "c++", "-E", "-MD", "-MF", depfile.path.string(),
                                        "-MT", "cxxdeps", source.string(), "-o", scan.path.string()});
    auto scan_key = [&] {
        require_success(preprocess, cwd, verbose);
        const auto preprocessed = read_file(scan.path);
        lossy_path = lossy_dependency_path(source, cwd, flags);
        if (lossy_path.empty() && has_lossy_line_marker(preprocessed)) {
            lossy_path = "line marker filename";
        }
        untracked = has_untracked_assembly(preprocessed);
        auto discover = [&](const fs::path& path) {
            if (discovered_inputs && !std::ranges::contains(discovered_inputs->paths, path))
                discovered_inputs->paths.push_back(path);
        };
        discover(cwd / source);
        if (discovered_inputs && (!lossy_path.empty() || untracked)) discovered_inputs->complete = false;
        std::vector<std::string> fields{"cxx-m0-compile-v6", compiler.string(), source.string(),
                                        output.string(), cwd.string(), tool_identity,
                                        environment_fingerprint(), hash_file(scan.path),
                                        hash_file(cwd / source), lossy_path};
        fields.insert(fields.end(), flags.begin(), flags.end());
        // Always hash the actual source, independently of the depfile spelling.
        // Expanded text alone loses macro spelling and source column semantics.
        // Only lossless depfiles can identify raw headers (including system ones).
        if (lossy_path.empty()) {
            for (const auto& path : dependency_paths(depfile.path, cwd)) {
                fields.push_back(path.string()); fields.push_back(hash_file(path));
                discover(path);
            }
        }
        return fingerprint(fields);
    };
    const auto key = scan_key();
    if (lossy_path.empty() && !untracked && is_current(output, key)) return false;
    if (!lossy_path.empty() && verbose) {
        std::cerr << "[uncached] Lossy or ambiguous compiler dependency paths: " << lossy_path << '\n';
    }
    if (untracked && verbose) std::cerr << "[uncached] Assembly input closure is not tracked\n";
    std::cerr << '[' << label << "] " << source.filename().string() << '\n';
    Temporary temp{temporary_for(output)};
    std::vector<std::string> compile{compiler.string()};
    compile.insert(compile.end(), flags.begin(), flags.end());
    compile.insert(compile.end(), {"-x", "c++", "-c", source.string(), "-o", temp.path.string()});
    require_success(compile, cwd, verbose);
    if (scan_key() != key) throw Error("Compilation inputs changed during the action; retry the build: " + source.string());
    publish(temp.path, output, key);
    return true;
}

bool file_action(const std::string& label, const std::vector<std::string>& args,
                 const std::vector<fs::path>& inputs, const fs::path& output,
                 const std::string& tool_identity, const fs::path& cwd, bool verbose,
                 bool cacheable) {
    if (args.empty() || std::find(args.begin() + 1, args.end(), output.string()) == args.end()) {
        throw Error("Action command must explicitly name its output as a standalone argument");
    }
    auto input_key = [&] {
        std::vector<std::string> fields{"cxx-m1-file-action-v2", label, output.string(), cwd.string(),
                                        tool_identity, environment_fingerprint()};
        fields.insert(fields.end(), args.begin(), args.end());
        for (const auto& input : inputs) { fields.push_back(input.string()); fields.push_back(hash_file(input)); }
        return fingerprint(fields);
    };
    const auto key = input_key();
    if (cacheable && is_current(output, key)) return false;
    fs::create_directories(output.parent_path());
    Temporary temp{temporary_for(output)};
    auto command = args;
    std::replace(command.begin() + 1, command.end(), output.string(), temp.path.string());
    // Archivers may update existing archives, so always start from an empty file set.
    std::error_code ec;
    fs::remove(temp.path, ec);
    std::cerr << '[' << label << "] " << (label == "custom" ? output.string() : output.filename().string()) << '\n';
    require_success(command, cwd, verbose);
    if (input_key() != key) throw Error("Action inputs changed during the action; retry the build: " + output.string());
    publish(temp.path, output, key);
    return true;
}
} // namespace cxx::detail
