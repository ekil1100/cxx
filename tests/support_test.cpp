#include "support.hpp"
#include <cxx/cxx.hpp>

#include <array>
#include <cerrno>
#include <cstdlib>
#include <iostream>
#include <sys/wait.h>
#include <unistd.h>

using namespace cxx::detail;

namespace {
void check(bool condition, const std::string& message) {
    if (!condition) throw Error("Test failed: " + message);
}
template<class Function> void rejects(Function function, const std::string& message) {
    bool rejected = false;
    try { function(); } catch (const std::exception&) { rejected = true; }
    check(rejected, message);
}

int spawn_worker(const std::string& executable, int iterations) {
    check(iterations > 0, "positive spawn iteration count");
    // A stuck gate must fail CI rather than leave the test waiting indefinitely.
    alarm(120);
    int child_nonzero = 0;
    int parent_errors = 0;
    const std::vector<std::string> command{executable, "--spawn-child"};
    const auto cwd = fs::current_path();
    for (int i = 0; i < iterations; ++i) {
        try {
            const int code = run_process(command, cwd);
            if (code != 0) {
                ++child_nonzero;
                std::cerr << "SPAWN child exit=" << code << " iteration=" << i << '\n';
            }
        } catch (const std::exception& error) {
            ++parent_errors;
            std::cerr << "SPAWN parent error=" << error.what() << " iteration=" << i << '\n';
        }
    }
    alarm(0);
    std::cout << "SPAWN iterations=" << iterations << " child_nonzero=" << child_nonzero
              << " parent_errors=" << parent_errors << '\n';
    return child_nonzero || parent_errors ? 1 : 0;
}

void concurrent_spawns(const std::string& executable) {
    // Separate processes exercise run_process's real fork path concurrently;
    // they also isolate its process-wide signal handlers from other callers.
    std::array<pid_t, 4> workers{};
    char* const args[] = {const_cast<char*>(executable.c_str()),
                         const_cast<char*>("--spawn-worker"), const_cast<char*>("250"), nullptr};
    const char* program = executable.c_str();
    std::cout.flush();
    std::cerr.flush();
    for (auto& worker : workers) {
        worker = fork();
        if (worker == 0) {
            if (setsid() < 0) _exit(125);
            execv(program, args);
            _exit(127);
        }
        if (worker < 0) break;
    }
    bool success = true;
    for (const auto worker : workers) {
        if (worker <= 0) { success = false; continue; }
        int status = 0;
        pid_t waited;
        do { waited = waitpid(worker, &status, 0); } while (waited < 0 && errno == EINTR);
        if (waited != worker || !WIFEXITED(status) || WEXITSTATUS(status) != 0) {
            success = false;
            std::cerr << "SPAWN worker=" << worker << " wait=" << waited << " status=" << status << '\n';
        }
    }
    check(success, "1000 concurrent run_process calls without child failures or parent exceptions");
}
}

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--spawn-child") {
        return getpgrp() == getpid() ? 0 : 44;
    }
    if (argc == 3 && std::string(argv[1]) == "--spawn-worker") {
        return spawn_worker(resolve_program(argv[0]).string(), std::stoi(argv[2]));
    }
    const auto root = fs::temp_directory_path() / ("cxx-unit-" + std::to_string(getpid()));
    try {
        fs::create_directories(root);
        const auto input = root / "input with space";
        const auto output = root / "output";
        write_atomic(input, "abc");
        check(read_file(input) == "abc", "atomic read/write");
        check(hash_file(input) == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad",
              "SHA-256 known vector");
        check(fingerprint({"a", "bc"}) != fingerprint({"ab", "c"}), "unambiguous field encoding");
        check(quote_cpp("a\n\"\\") == "\"a\\012\\\"\\\\\"", "C++ string escaping");
        rejects([&] { read_file(root / "missing"); }, "missing input");
        {
            ProjectLock lock(root / "lock");
            rejects([&] { ProjectLock other(root / "lock"); }, "concurrent writer rejected");
        }
        { ProjectLock lock(root / "lock"); }
        const auto copy = resolve_program("/bin/cp");
        const std::vector<std::string> args{copy.string(), input.string(), output.string()};
        check(file_action("copy", args, {input}, output, hash_file(copy), root, false), "first action");
        check(!file_action("copy", args, {input}, output, hash_file(copy), root, false), "unchanged action");
        write_atomic(output, "corrupt");
        check(file_action("copy", args, {input}, output, hash_file(copy), root, false), "corrupt output repaired");
        rejects([&] {
            file_action("failure", {"/bin/sh", "-c", "exit 3", output.string()},
                        {input}, output, "test", root, false);
        }, "failed action rejected");
        check(read_file(output) == "abc", "failed action preserves previous output");
        rejects([&] {
            file_action("missing-output", {copy.string(), input.string()}, {input},
                        root / "missing-output", "test", root, false);
        }, "action without explicit output rejected");
        check(!fs::exists(root / "missing-output"), "invalid action creates no output");
        check(run_process({"/bin/sh", "-c", "exit 7"}, root) == 7, "exit code forwarding");
        check(run_process({"/missing-cxx-command"}, root) == 127, "exec failure exit code");
        check(run_process({"/bin/sh"}, root / "missing-directory") == 126, "chdir failure exit code");
        concurrent_spawns(resolve_program(argv[0]).string());
        fs::create_directories(root / "tools/nested");
        fs::create_directory_symlink("tools/nested", root / "tool-link");
        write_atomic(root / "tools/tool", "#!/bin/sh\nexit 23\n");
        fs::permissions(root / "tools/tool", fs::perms::owner_exec, fs::perm_options::add);
        fs::create_symlink("tool", root / "tools/clang++");
        write_atomic(root / "clang++", "#!/bin/sh\nexit 11\n");
        fs::permissions(root / "clang++", fs::perms::owner_exec, fs::perm_options::add);
        const auto spelling = root / "tool-link/../clang++";
        const auto resolved = resolve_program(spelling.string());
        check(resolved == spelling, "explicit tool path preserves symlinks and parent traversal");
        check(run_process({resolved.string()}, root) == 23, "explicit tool selects actual filesystem path");
        const char* previous_path = std::getenv("PATH");
        const bool had_path = previous_path != nullptr;
        const std::string saved_path = had_path ? previous_path : "";
        check(setenv("PATH", (root / "tool-link/..").c_str(), 1) == 0, "set tool search path");
        const auto searched = resolve_program("clang++");
        if (had_path) setenv("PATH", saved_path.c_str(), 1);
        else unsetenv("PATH");
        check(searched == spelling, "PATH tool preserves symlink traversal and driver name");
        check(run_process({searched.string()}, root) == 23, "PATH tool selects actual filesystem path");
        cxx::Build build({"", "", "17", root.string()});
        auto& library = build.static_library("lib");
        auto& app = build.executable("app");
        app.link(library);
        for (int i = 0; i < 100; ++i) build.static_library("lib_" + std::to_string(i));
        check(app.dependencies().front().first == &library, "stable target ownership");
        rejects([&] { build.executable("app"); }, "duplicate target");
        rejects([&] { build.static_library("APP"); }, "ASCII-folded duplicate across target kinds");
        rejects([&] { build.executable("aPp"); }, "mixed-case duplicate target");
        auto& mixed_case = build.executable("App_Helper");
        check(mixed_case.name() == "App_Helper", "distinct mixed-case target retains spelling");
        rejects([&] { build.executable("../escape"); }, "unsafe target name");
        rejects([&] { library.link(app); }, "executable dependency");
        cxx::Build other({"", "", "17", root.string()});
        auto& external = other.static_library("external");
        rejects([&] { app.link(external); }, "cross-Build dependency");
        rejects([&] { app.cpp_standard("invalid"); }, "invalid standard");
        check(app.optimization() == cxx::Optimization::default_ && !app.warnings_enabled(),
              "compile settings preserve compiler defaults");
        check(&app.optimization(cxx::Optimization::speed).warnings().system_include("vendor") == &app,
              "compile settings support chaining");
        check(app.optimization() == cxx::Optimization::speed && app.warnings_enabled(), "compile settings stored");
        check(app.system_includes().front().visibility == cxx::Visibility::private_, "system include defaults private");
        app.optimization(cxx::Optimization::none).warnings(false);
        check(app.optimization() == cxx::Optimization::none && !app.warnings_enabled(), "compile settings replaced");
        rejects([&] { app.optimization(static_cast<cxx::Optimization>(99)); }, "invalid optimization");
        rejects([&] { app.system_include(""); }, "empty system include");
        rejects([&] { app.system_include(std::string{"x\0y", 3}); }, "NUL system include");
        rejects([&] { app.system_include("vendor", static_cast<cxx::Visibility>(99)); }, "invalid system include visibility");
        rejects([&] { app.source(std::string{"x\0y", 3}); }, "NUL source path rejected");
        rejects([&] { app.define(std::string{"x\0y", 3}); }, "NUL definition rejected");
        fs::remove_all(root);
        std::cout << "PASS: support and SDK unit tests\n";
        return 0;
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        std::error_code ec;
        fs::remove_all(root, ec);
        return 1;
    }
}
