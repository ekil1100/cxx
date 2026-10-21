#include "support.hpp"
#include <cxx/cxx.hpp>
#include <gtest/gtest.h>

#include <algorithm>
#include <array>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <sys/wait.h>
#include <unistd.h>

using namespace cxx::detail;

namespace {
std::string test_program;

fs::path test_compiler() {
    const char* compiler = std::getenv("CXX_BOOTSTRAP_COMPILER");
    return resolve_program(compiler ? compiler : "clang++");
}

class TemporaryDirectory {
public:
    TemporaryDirectory()
        : path_((fs::temp_directory_path() / "cxx-unit-XXXXXX").string()) {
        if (!mkdtemp(path_.data())) {
            throw Error("Cannot create test directory: " + std::string(std::strerror(errno)));
        }
    }
    ~TemporaryDirectory() {
        std::error_code ec;
        fs::remove_all(path_, ec);
    }
    TemporaryDirectory(const TemporaryDirectory&) = delete;
    TemporaryDirectory& operator=(const TemporaryDirectory&) = delete;
    const std::string& path() const { return path_; }
private:
    std::string path_;
};

class SupportTest : public testing::Test {
private:
    TemporaryDirectory directory_;
protected:
    const fs::path root = directory_.path();
};

// Construct before changing PATH so fatal assertions and exceptions restore it.
class PathRestore {
public:
    PathRestore() {
        const char* value = std::getenv("PATH");
        had_path_ = value != nullptr;
        if (had_path_) saved_path_ = value;
    }
    ~PathRestore() {
        const int result = had_path_ ? setenv("PATH", saved_path_.c_str(), 1) : unsetenv("PATH");
        EXPECT_EQ(result, 0) << "Cannot restore PATH: " << std::strerror(errno);
    }
    PathRestore(const PathRestore&) = delete;
    PathRestore& operator=(const PathRestore&) = delete;
private:
    bool had_path_;
    std::string saved_path_;
};

class FileActionTest : public SupportTest {
protected:
    void SetUp() override {
        write_atomic(input, "abc");
        copy = resolve_program("/bin/cp");
        tool_identity = hash_file(copy);
        args = {copy.string(), input.string(), output.string()};
    }
    const fs::path input = root / "input with space";
    const fs::path output = root / "output";
    fs::path copy;
    std::string tool_identity;
    std::vector<std::string> args;
};

class ToolResolutionTest : public SupportTest {
protected:
    void SetUp() override {
        fs::create_directories(root / "tools/nested");
        fs::create_directory_symlink("tools/nested", root / "tool-link");
        write_atomic(root / "tools/tool", "#!/bin/sh\nexit 23\n");
        fs::permissions(root / "tools/tool", fs::perms::owner_exec, fs::perm_options::add);
        fs::create_symlink("tool", root / "tools/clang++");
        write_atomic(root / "clang++", "#!/bin/sh\nexit 11\n");
        fs::permissions(root / "clang++", fs::perms::owner_exec, fs::perm_options::add);
    }
    const fs::path spelling = root / "tool-link/../clang++";
};

class BuildTest : public SupportTest {
protected:
    cxx::Build build{{"", "", "17", root.string()}};
};

class CompileSettingsTest : public BuildTest {
protected:
    cxx::Target& app = build.executable("app");
};

int spawn_worker(const std::string& executable, int iterations) {
    if (iterations <= 0) {
        std::cerr << "SPAWN iteration count must be positive\n";
        return 1;
    }
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

TEST_F(SupportTest, AtomicReadWrite) {
    const auto input = root / "input with space";
    write_atomic(input, "abc");
    EXPECT_EQ(read_file(input), "abc");
}

TEST_F(SupportTest, Sha256KnownVector) {
    const auto input = root / "input with space";
    write_atomic(input, "abc");
    EXPECT_EQ(hash_file(input), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
}

TEST(SupportEncodingTest, FingerprintHasUnambiguousFieldEncoding) {
    EXPECT_NE(fingerprint({"a", "bc"}), fingerprint({"ab", "c"}));
}

TEST(SupportEncodingTest, QuoteCppEscapesNewlineQuoteAndBackslash) {
    EXPECT_EQ(quote_cpp("a\n\"\\"), "\"a\\012\\\"\\\\\"");
}

TEST_F(SupportTest, MissingInputIsRejected) {
    EXPECT_THROW(read_file(root / "missing"), std::exception);
}

TEST_F(SupportTest, ProjectLockRejectsConcurrentWriter) {
    ProjectLock lock(root / "lock");
    EXPECT_THROW({ ProjectLock other(root / "lock"); }, std::exception);
}

TEST_F(SupportTest, ProjectLockCanBeReacquiredAfterRelease) {
    { ProjectLock lock(root / "lock"); }
    EXPECT_NO_THROW({ ProjectLock lock(root / "lock"); });
}

TEST_F(FileActionTest, FirstActionRuns) {
    EXPECT_TRUE(file_action("copy", args, {input}, output, tool_identity, root, false));
}

TEST_F(FileActionTest, UnchangedActionIsCached) {
    ASSERT_TRUE(file_action("copy", args, {input}, output, tool_identity, root, false));
    EXPECT_FALSE(file_action("copy", args, {input}, output, tool_identity, root, false));
}

TEST_F(FileActionTest, CorruptOutputIsRepaired) {
    ASSERT_TRUE(file_action("copy", args, {input}, output, tool_identity, root, false));
    write_atomic(output, "corrupt");
    EXPECT_TRUE(file_action("copy", args, {input}, output, tool_identity, root, false));
    EXPECT_EQ(read_file(output), "abc");
}

TEST_F(FileActionTest, FailedActionPreservesPreviousOutput) {
    ASSERT_TRUE(file_action("copy", args, {input}, output, tool_identity, root, false));
    EXPECT_THROW(file_action("failure", {"/bin/sh", "-c", "exit 3", output.string()},
                             {input}, output, "test", root, false), std::exception);
    EXPECT_EQ(read_file(output), "abc");
}

TEST_F(FileActionTest, MissingExplicitOutputIsRejectedWithoutCreatingOutput) {
    EXPECT_THROW(file_action("missing-output", {copy.string(), input.string()}, {input},
                             root / "missing-output", "test", root, false), std::exception);
    EXPECT_FALSE(fs::exists(root / "missing-output"));
}

TEST_F(SupportTest, DependencyScanHandoffPreservesPathsAndCompleteness) {
    const auto file = root / "bootstrap-inputs";
    const std::vector<fs::path> paths{root / "space and quote\".hpp", root / "line\nbreak.hpp",
                                      root / "literal\\backslash.hpp"};
    for (const bool complete : {true, false}) {
        write_dependency_scan(file, {paths, complete});
        const auto scan = read_dependency_scan(file);
        EXPECT_EQ(scan.paths, paths);
        EXPECT_EQ(scan.complete, complete);
    }
}

TEST_F(SupportTest, InvalidDependencyScanHandoffIsRejected) {
    const auto file = root / "bootstrap-inputs";
    EXPECT_THROW(read_dependency_scan(file), std::exception);
    for (const auto* contents : {"", "unknown", "1unterminated"}) {
        write_atomic(file, contents);
        EXPECT_THROW(read_dependency_scan(file), std::exception);
    }
}

TEST_F(SupportTest, CompileReportsTransitiveInputsOnCacheHit) {
    const auto compiler = test_compiler();
    const auto identity = hash_file(compiler);
    const auto source = root / "unit.cpp";
    const auto header = root / "value.hpp";
    const auto transitive = root / "nested.hpp";
    const auto output = root / "unit.o";
    write_atomic(source, "#include \"value.hpp\"\nint value() { return VALUE; }\n");
    write_atomic(header, "#include \"nested.hpp\"\n");
    write_atomic(transitive, "#define VALUE 7\n");
    for (const bool first : {true, false}) {
        DependencyScan scan;
        EXPECT_EQ(compile_cpp(compiler, source, output, {"-std=c++17"}, identity, root,
                              false, "compile", &scan), first);
        EXPECT_TRUE(scan.complete);
        for (const auto& path : {source, header, transitive})
            EXPECT_TRUE(std::ranges::contains(scan.paths, path)) << path;
    }
}

TEST_F(SupportTest, LossyCompileKeepsKnownInputsAndIncompleteStateAcrossCacheHit) {
    const auto compiler = test_compiler();
    const auto identity = hash_file(compiler);
    const auto source = root / "unit.cpp";
    const auto output = root / "unit.o";
    write_atomic(source, "int value() { return 7; }\n");
    DependencyScan scan;
    ASSERT_TRUE(compile_cpp(compiler, source, output, {}, identity, root, false, "compile", &scan));
    ASSERT_TRUE(scan.complete);
    const auto lossy_source = root / "lossy\\directory/unit.cpp";
    write_atomic(lossy_source, "#include \"value.hpp\"\nint other() { return VALUE; }\n");
    write_atomic(lossy_source.parent_path() / "value.hpp", "#define VALUE 9\n");
    EXPECT_TRUE(compile_cpp(compiler, lossy_source, root / "lossy.o", {}, identity, root,
                            false, "compile", &scan));
    EXPECT_FALSE(scan.complete);
    EXPECT_TRUE(std::ranges::contains(scan.paths, source));
    EXPECT_TRUE(std::ranges::contains(scan.paths, lossy_source));
    EXPECT_FALSE(compile_cpp(compiler, source, output, {}, identity, root, false, "compile", &scan));
    EXPECT_FALSE(scan.complete);
}

TEST_F(SupportTest, UntrackedAssemblyReportsIncompleteInputs) {
    const auto compiler = test_compiler();
    const auto source = root / "unit.cpp";
    write_atomic(source, "asm(\".byte 0\");\n");
    DependencyScan scan;
    EXPECT_TRUE(compile_cpp(compiler, source, root / "unit.o", {}, hash_file(compiler), root,
                            false, "compile", &scan));
    EXPECT_FALSE(scan.complete);
    EXPECT_TRUE(std::ranges::contains(scan.paths, source));
}

TEST_F(SupportTest, ProcessForwardsExitCode) {
    EXPECT_EQ(run_process({"/bin/sh", "-c", "exit 7"}, root), 7);
}

TEST_F(SupportTest, ProcessExecFailureReturns127) {
    EXPECT_EQ(run_process({"/missing-cxx-command"}, root), 127);
}

TEST_F(SupportTest, ProcessChdirFailureReturns126) {
    EXPECT_EQ(run_process({"/bin/sh"}, root / "missing-directory"), 126);
}

TEST_F(SupportTest, ConcurrentSpawningRuns1000ChildrenWithoutFailures) {
    const auto executable = resolve_program(test_program).string();
    // Separate processes exercise run_process's real fork path concurrently;
    // they also isolate its process-wide signal handlers from other callers.
    std::array<pid_t, 4> workers{};
    char* const args[] = {const_cast<char*>(executable.c_str()),
                         const_cast<char*>("--spawn-worker"), const_cast<char*>("250"), nullptr};
    const char* program = executable.c_str();
    const char* directory = root.c_str();
    std::cout.flush();
    std::cerr.flush();
    for (auto& worker : workers) {
        worker = fork();
        if (worker == 0) {
            if (setsid() < 0) _exit(125);
            if (chdir(directory) < 0) _exit(126);
            execv(program, args);
            _exit(127);
        }
        if (worker < 0) break;
    }
    // Use nonfatal assertions so every successfully forked worker is reaped.
    for (const auto worker : workers) {
        EXPECT_GT(worker, 0) << "Failed to start all four spawn workers";
        if (worker <= 0) continue;
        int status = 0;
        pid_t waited;
        do { waited = waitpid(worker, &status, 0); } while (waited < 0 && errno == EINTR);
        EXPECT_EQ(waited, worker) << "SPAWN worker=" << worker << " status=" << status;
        if (waited != worker) continue;
        EXPECT_TRUE(WIFEXITED(status)) << "SPAWN worker=" << worker << " status=" << status;
        if (WIFEXITED(status)) {
            EXPECT_EQ(WEXITSTATUS(status), 0) << "SPAWN worker=" << worker;
        }
    }
}

TEST_F(ToolResolutionTest, ExplicitPathPreservesSymlinksAndParentTraversal) {
    const auto resolved = resolve_program(spelling.string());
    EXPECT_EQ(resolved, spelling);
    EXPECT_EQ(run_process({resolved.string()}, root), 23);
}

TEST_F(ToolResolutionTest, PathSearchPreservesSymlinkTraversalAndDriverName) {
    const PathRestore restore;
    ASSERT_EQ(setenv("PATH", (root / "tool-link/..").c_str(), 1), 0);
    const auto searched = resolve_program("clang++");
    EXPECT_EQ(searched, spelling);
    EXPECT_EQ(run_process({searched.string()}, root), 23);
}

TEST_F(ToolResolutionTest, ScanTestsUseSelectedCompilerAcrossWorkingDirectories) {
    const auto compiler = test_compiler().string();
    const auto executable = resolve_program(test_program).string();
    const auto log = root / "compiler.log";
    write_atomic(root / "tools/tool",
                 "#!/bin/sh\n"
                 "printf '%s\\n' \"$0\" >> \"$CXX_UNIT_COMPILER_LOG\"\n"
                 "exec \"$CXX_UNIT_REAL_COMPILER\" \"$@\"\n");
    fs::permissions(root / "tools/tool", fs::perms::owner_exec, fs::perm_options::add);
    fs::create_directory(root / "empty-path");
    const char* filter =
        "--gtest_filter=SupportTest.CompileReportsTransitiveInputsOnCacheHit:"
        "SupportTest.LossyCompileKeepsKnownInputsAndIncompleteStateAcrossCacheHit:"
        "SupportTest.UntrackedAssemblyReportsIncompleteInputs";
    // The alternate clang++ exits 11; the empty directory has no clang++.
    // An empty explicit selection must fail instead of falling back to PATH.
    for (const auto* mode : {"alternate", "missing", "empty-selection"}) {
        SCOPED_TRACE(mode);
        write_atomic(log, "");
        const bool empty_selection = std::string(mode) == "empty-selection";
        const auto path = empty_selection ? root / "tools"
            : std::string(mode) == "missing" ? root / "empty-path" : root;
        std::cout.flush();
        std::cerr.flush();
        const auto child = fork();
        ASSERT_GE(child, 0);
        if (child == 0) {
            // The deadline survives exec and bounds all three real scan tests.
            alarm(60);
            if (chdir(root.c_str()) < 0 ||
                setenv("PATH", path.c_str(), 1) < 0 ||
                setenv("CXX_BOOTSTRAP_COMPILER",
                       empty_selection ? "" : "tool-link/../clang++", 1) < 0 ||
                setenv("CXX_UNIT_REAL_COMPILER", compiler.c_str(), 1) < 0 ||
                setenv("CXX_UNIT_COMPILER_LOG", log.c_str(), 1) < 0) _exit(125);
            execl(executable.c_str(), executable.c_str(), filter, nullptr);
            _exit(127);
        }
        int status = 0;
        pid_t waited;
        do { waited = waitpid(child, &status, 0); } while (waited < 0 && errno == EINTR);
        ASSERT_EQ(waited, child);
        ASSERT_TRUE(WIFEXITED(status)) << "status=" << status;
        EXPECT_EQ(WEXITSTATUS(status), empty_selection ? 1 : 0);
        const auto invocations = read_file(log);
        if (empty_selection) {
            EXPECT_TRUE(invocations.empty());
        } else {
            EXPECT_FALSE(invocations.empty());
            std::size_t offset = 0;
            // Match the child's physical cwd while preserving compiler path traversal.
            const auto expected = (fs::canonical(root) / "tool-link/../clang++").string() + "\n";
            while (offset < invocations.size()) {
                ASSERT_EQ(invocations.substr(offset, expected.size()), expected);
                offset += expected.size();
            }
        }
    }
}

TEST_F(BuildTest, TargetOwnershipRemainsStableAsBuildGrows) {
    auto& library = build.static_library("lib");
    auto& app = build.executable("app");
    app.link(library);
    for (int i = 0; i < 100; ++i) build.static_library("lib_" + std::to_string(i));
    ASSERT_FALSE(app.dependencies().empty());
    EXPECT_EQ(app.dependencies().front().first, &library);
}

TEST_F(BuildTest, DuplicateTargetIsRejected) {
    build.executable("app");
    EXPECT_THROW(build.executable("app"), std::exception);
}

TEST_F(BuildTest, AsciiFoldedDuplicateAcrossTargetKindsIsRejected) {
    build.executable("app");
    EXPECT_THROW(build.static_library("APP"), std::exception);
}

TEST_F(BuildTest, MixedCaseDuplicateTargetIsRejected) {
    build.executable("app");
    EXPECT_THROW(build.executable("aPp"), std::exception);
}

TEST_F(BuildTest, DistinctMixedCaseTargetRetainsSpelling) {
    build.executable("app");
    auto& mixed_case = build.executable("App_Helper");
    EXPECT_EQ(mixed_case.name(), "App_Helper");
}

TEST_F(BuildTest, UnsafeTargetNameIsRejected) {
    EXPECT_THROW(build.executable("../escape"), std::exception);
}

TEST_F(BuildTest, ExecutableDependencyIsRejected) {
    auto& library = build.static_library("lib");
    auto& app = build.executable("app");
    EXPECT_THROW(library.link(app), std::exception);
}

TEST_F(BuildTest, CrossBuildDependencyIsRejected) {
    auto& app = build.executable("app");
    cxx::Build other({"", "", "17", root.string()});
    auto& external = other.static_library("external");
    EXPECT_THROW(app.link(external), std::exception);
}

TEST_F(CompileSettingsTest, InvalidStandardIsRejected) {
    EXPECT_THROW(app.cpp_standard("invalid"), std::exception);
}

TEST_F(CompileSettingsTest, DefaultsPreserveCompilerSettings) {
    EXPECT_EQ(app.optimization(), cxx::Optimization::default_);
    EXPECT_FALSE(app.warnings_enabled());
}

TEST_F(CompileSettingsTest, SettersSupportChainingAndStoreSettings) {
    EXPECT_EQ(&app.optimization(cxx::Optimization::speed).warnings().system_include("vendor"), &app);
    EXPECT_EQ(app.optimization(), cxx::Optimization::speed);
    EXPECT_TRUE(app.warnings_enabled());
}

TEST_F(CompileSettingsTest, SystemIncludeDefaultsToPrivate) {
    app.system_include("vendor");
    ASSERT_FALSE(app.system_includes().empty());
    EXPECT_EQ(app.system_includes().front().visibility, cxx::Visibility::private_);
}

TEST_F(CompileSettingsTest, SettingsCanBeReplaced) {
    app.optimization(cxx::Optimization::speed).warnings();
    app.optimization(cxx::Optimization::none).warnings(false);
    EXPECT_EQ(app.optimization(), cxx::Optimization::none);
    EXPECT_FALSE(app.warnings_enabled());
}

TEST_F(CompileSettingsTest, InvalidOptimizationIsRejected) {
    EXPECT_THROW(app.optimization(static_cast<cxx::Optimization>(99)), std::exception);
}

TEST_F(CompileSettingsTest, EmptySystemIncludeIsRejected) {
    EXPECT_THROW(app.system_include(""), std::exception);
}

TEST_F(CompileSettingsTest, NulSystemIncludeIsRejected) {
    EXPECT_THROW(app.system_include(std::string{"x\0y", 3}), std::exception);
}

TEST_F(CompileSettingsTest, InvalidSystemIncludeVisibilityIsRejected) {
    EXPECT_THROW(app.system_include("vendor", static_cast<cxx::Visibility>(99)), std::exception);
}

TEST_F(CompileSettingsTest, NulSourcePathIsRejected) {
    EXPECT_THROW(app.source(std::string{"x\0y", 3}), std::exception);
}

TEST_F(CompileSettingsTest, NulDefinitionIsRejected) {
    EXPECT_THROW(app.define(std::string{"x\0y", 3}), std::exception);
}
} // namespace

int main(int argc, char** argv) {
    if (argc == 2 && std::string(argv[1]) == "--spawn-child") {
        return getpgrp() == getpid() ? 0 : 44;
    }
    if (argc == 3 && std::string(argv[1]) == "--spawn-worker") {
        try {
            return spawn_worker(resolve_program(argv[0]).string(), std::stoi(argv[2]));
        } catch (const std::exception& error) {
            std::cerr << "SPAWN worker error=" << error.what() << '\n';
            return 1;
        }
    }
    test_program = argv[0];
    testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
