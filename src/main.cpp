#include "support.hpp"
#include <toml.hpp>

#include <cstdlib>
#include <iostream>
#include <string>
#include <vector>

#ifndef CXX_SDK_ROOT
#error "Define CXX_SDK_ROOT to the source SDK location when building the launcher"
#endif

namespace {
namespace fs = std::filesystem;
using namespace cxx::detail;

struct Options {
    std::string command;
    std::vector<std::string> arguments;
    bool verbose = false;
};

Options parse_options(int argc, char** argv) {
    if (argc < 2) throw Error("Expected a command; use --help");
    Options options{argv[1], {}, false};
    if (options.command == "--help" || options.command == "--version") {
        if (argc != 2) throw Error(options.command + " takes no arguments");
        return options;
    }
    if (options.command == "new") {
        if (argc != 3 || !argv[2][0] || argv[2][0] == '-')
            throw Error("Usage: cxx new <directory> (use ./ for a directory starting with '-')");
        options.arguments.emplace_back(argv[2]);
        return options;
    }
    if (options.command == "test" || options.command == "install") {
        bool filter = false, prefix = false;
        for (int i = 2; i < argc; ++i) {
            const std::string argument = argv[i];
            if (argument == "-v") {
                if (options.verbose) throw Error("Duplicate option: -v");
                options.verbose = true;
            } else if (argument == "--prefix" && options.command == "install") {
                if (prefix) throw Error("Duplicate option: --prefix");
                if (++i == argc || !argv[i][0] || argv[i][0] == '-')
                    throw Error("install requires --prefix <nonempty directory>; use ./ for a name starting with '-'");
                prefix = true;
                options.arguments.push_back(argument);
                options.arguments.emplace_back(argv[i]);
                continue;
            } else if (options.command == "test" && !argument.starts_with('-')) {
                if (filter) throw Error("test accepts at most one filter");
                filter = true;
            } else throw Error("Unknown argument: " + argument);
            options.arguments.push_back(argument);
        }
        if (options.command == "install" && !prefix)
            throw Error("install requires an explicit --prefix <directory>");
        return options;
    }
    if (options.command != "build" && options.command != "run")
        throw Error("Unknown command: " + options.command + "; supported commands: new, build, run, test, install");
    bool program_arguments = false;
    unsigned targets = 0;
    for (int i = 2; i < argc; ++i) {
        const std::string argument = argv[i];
        if (program_arguments) {
            options.arguments.push_back(argument);
            continue;
        }
        if (argument == "--") {
            if (options.command != "run") throw Error("Only run accepts -- program arguments");
            program_arguments = true;
        } else if (argument == "-v") {
            if (options.verbose) throw Error("Duplicate option: -v");
            options.verbose = true;
        } else {
            if (argument.empty() || argument.starts_with('-'))
                throw Error("Unknown argument: " + argument);
            if (++targets > 1 && options.command == "run")
                throw Error("run accepts at most one target");
        }
        options.arguments.push_back(argument);
    }
    return options;
}

void print_help() {
    std::cout << R"(Usage:
  cxx new <directory>
  cxx build [targets...] [-v]
  cxx run [target] [-v] [-- args...]
  cxx test [filter] [-v]
  cxx install --prefix <directory> [-v]
  cxx --help
  cxx --version

M0: Edition 0; user C++17/20/23/26; llvm preset, version omitted or "system".
CXX and its Runtime require a C++23-capable host compiler.
Tests use a literal name substring filter and run sequentially in the project root.
Install is opt-in; relative prefixes are relative to the project root.
Install uses separate relocatable artifacts and replaces files individually.
No uninstall, manifest, multi-file rollback, or SDK package export is provided.
Version fields are TOML strings; edition defaults to "0", cpp.version to "17".
Tool priority: CXX_BOOTSTRAP_COMPILER / CXX_BOOTSTRAP_ARCHIVER (development
only) > compiler.path / archiver.path > PATH clang++ / llvm-ar (fallback ar).
Configured paths are absolute or project-relative. Development overrides
accept PATH names as well. CXX_SDK_ROOT in the process environment overrides
the compiled-in SDK root; relative SDK paths use the invocation directory.
Toolchain downloads, resolution Lock and extended configuration require M2.
)";
}

void create_project(const fs::path& directory) {
    // symlink_status also rejects dangling symlinks without following them.
    if (fs::exists(fs::symlink_status(directory)))
        throw Error("Directory already exists: " + directory.string());
    if (!fs::create_directories(directory))
        throw Error("Cannot create new directory: " + directory.string());
    write_atomic(directory / "cxxrc.toml",
                 "[cxx]\nedition = \"0\"\n\n[cpp]\nversion = \"17\"\n\n"
                 "[preset]\nname = \"llvm\"\nversion = \"system\"\n");
    write_atomic(directory / "build.cpp",
                 "#include <cxx/cxx.hpp>\n\n"
                 "void build(cxx::Build& b) {\n"
                 "    b.executable(\"hello\").source(\"src/main.cpp\");\n}\n");
    write_atomic(directory / "src/main.cpp",
                 "#include <iostream>\n\nint main() {\n"
                 "    std::cout << \"Hello, world!\\n\";\n}\n");
    std::cout << "Created project: " << directory.string() << '\n';
}

fs::path find_project(fs::path directory) {
    for (;;) {
        const auto config = directory / "cxxrc.toml";
        if (fs::exists(fs::symlink_status(config))) {
            if (!fs::is_regular_file(config))
                throw Error("Project configuration is not a regular file: " + config.string());
            return directory;
        }
        const auto parent = directory.parent_path();
        if (parent == directory) throw Error("No cxxrc.toml found in this directory or its parents");
        directory = parent;
    }
}

[[noreturn]] void unsupported(const std::string& field) {
    throw Error("Unsupported configuration: " + field + "; M2 support is not implemented");
}

struct Configuration {
    std::string standard;
    std::string compiler;
    std::string archiver;
};

std::string string_field(const toml::table& config, const char* section,
                         const char* field, const std::string& fallback) {
    const auto node = config[section][field];
    if (!node) return fallback;
    const auto value = node.value<std::string>();
    if (!value || value->empty() || value->contains('\0'))
        throw Error(std::string(section) + "." + field + " must be a nonempty TOML string without NUL");
    return *value;
}

Configuration read_configuration(const fs::path& path) {
    const auto config = toml::parse_file(path.string());
    for (const auto& [key, node] : config) {
        const std::string section(key.str());
        if (section != "cxx" && section != "cpp" && section != "preset" &&
            section != "compiler" && section != "archiver") unsupported(section);
        const auto* table = node.as_table();
        if (!table) throw Error("Configuration section must be a table: " + section);
        for (const auto& [field, value] : *table) {
            (void)value;
            const auto name = field.str();
            const bool allowed = (section == "cxx" && name == "edition") ||
                (section == "cpp" && name == "version") ||
                (section == "preset" && (name == "name" || name == "version")) ||
                ((section == "compiler" || section == "archiver") && name == "path");
            if (!allowed) unsupported(section + "." + std::string(name));
        }
    }
    const auto edition = string_field(config, "cxx", "edition", "0");
    if (edition != "0") unsupported("cxx.edition = " + edition + " (M0 requires 0)");
    const auto standard = string_field(config, "cpp", "version", "17");
    if (standard != "17" && standard != "20" && standard != "23" && standard != "26")
        unsupported("cpp.version = " + standard + " (M0 supports 17, 20, 23, 26)");
    const auto preset = string_field(config, "preset", "name", "");
    if (preset.empty()) throw Error("Missing required configuration: preset.name");
    if (preset != "llvm") unsupported("preset.name = " + preset + " (M0 requires llvm)");
    const auto version = string_field(config, "preset", "version", "system");
    if (version != "system")
        unsupported("preset.version = " + version + " (M0 uses system tools; downloads and Lock require M2)");
    const auto compiler = string_field(config, "compiler", "path", "");
    const auto archiver = string_field(config, "archiver", "path", "");
    if (compiler.contains('$') || archiver.contains('$'))
        unsupported("tool path environment expansion");
    return {standard, compiler, archiver};
}

fs::path resolve_tool(const char* override_name, const std::string& configured,
                      const fs::path& root, bool compiler) {
    if (const char* override_value = std::getenv(override_name)) {
        if (!*override_value) throw Error(std::string(override_name) + " must not be empty");
        return resolve_program(override_value);
    }
    if (!configured.empty()) return resolve_program((root / fs::path(configured)).string());
    if (compiler) return resolve_program("clang++");
    try {
        return resolve_program("llvm-ar");
    } catch (const Error&) {
        return resolve_program("ar");
    }
}

fs::path sdk_root(const fs::path& invocation_directory) {
    const char* override_value = std::getenv("CXX_SDK_ROOT");
    const std::string location = override_value ? override_value : CXX_SDK_ROOT;
    if (location.empty()) throw Error("CXX_SDK_ROOT must not be empty");
    const auto root = fs::weakly_canonical(invocation_directory / fs::path(location));
    for (const auto* relative : {"include/cxx/cxx.hpp", "src/runtime.hpp", "src/runtime.cpp",
                                 "src/support.hpp", "src/support.cpp", "third_party/picosha2/picosha2.h"}) {
        if (!fs::is_regular_file(root / relative))
            throw Error("Invalid CXX_SDK_ROOT: missing file " + (root / relative).string());
    }
    return root;
}

std::string tool_identity(const fs::path& tool) {
    // Preserve the invocation name (e.g. clang++), while hashing the actual executable.
    return fingerprint({tool.string(), hash_file(tool)});
}

int bootstrap(const Options& options, const fs::path& invocation_directory) {
    const auto root = find_project(invocation_directory);
    fs::current_path(root);
    ProjectLock lock(root / ".cxx/state/build.lock");
    const auto config = read_configuration(root / "cxxrc.toml");
    const auto compiler = resolve_tool("CXX_BOOTSTRAP_COMPILER", config.compiler, root, true);
    const auto archiver = resolve_tool("CXX_BOOTSTRAP_ARCHIVER", config.archiver, root, false);
    const auto sdk = sdk_root(invocation_directory);
    const auto build_script = root / "build.cpp";
    if (!fs::is_regular_file(build_script)) throw Error("Missing build script: " + build_script.string());
    const auto compiler_id = tool_identity(compiler);
    const auto archiver_id = tool_identity(archiver);
    const auto directory = root / ".cxx/driver";
    const auto runtime_object = directory / "runtime.o";
    const auto support_object = directory / "support.o";
    const auto archive = directory / "runtime.a";
    const auto script = directory / "script.cpp";
    const auto script_object = directory / "script.o";
    const auto driver = directory / "driver";
    const auto bootstrap_inputs = directory / "inputs";
    DependencyScan dependencies;
    const std::vector<std::string> runtime_flags{
        "-std=c++23", "-I" + (sdk / "include").string(), "-I" + (sdk / "src").string(),
        "-I" + (sdk / "third_party/picosha2").string()};
    // compile_cpp scans and hashes the original TU and all included SDK headers
    // on every invocation. No launcher binary/version hash enters these keys.
    compile_cpp(compiler, sdk / "src/runtime.cpp", runtime_object, runtime_flags,
                compiler_id, root, options.verbose, "runtime compile", &dependencies);
    compile_cpp(compiler, sdk / "src/support.cpp", support_object, runtime_flags,
                compiler_id, root, options.verbose, "runtime compile", &dependencies);
    file_action("runtime archive",
                {archiver.string(), "rcs", archive.string(), runtime_object.string(), support_object.string()},
                {runtime_object, support_object}, archive,
                fingerprint({compiler_id, archiver_id}), root, options.verbose);
    const std::string contents =
        "#include \"runtime.hpp\"\n#include \"../../build.cpp\""
        "\n\nint main(int argc, char** argv) {\n"
        "    return cxx::detail::driver_main(argc, argv, &build, cxx::Environment{" +
        quote_cpp(compiler.string()) + ", " + quote_cpp(archiver.string()) + ", " +
        quote_cpp(config.standard) + ", " + quote_cpp(root.string()) + "}, " +
        quote_cpp(bootstrap_inputs.string()) + ");\n}\n";
    if (!fs::is_regular_file(script) || read_file(script) != contents) write_atomic(script, contents);
    const std::vector<std::string> script_flags{
        "-std=c++" + config.standard, "-I" + (sdk / "include").string(), "-I" + (sdk / "src").string()};
    compile_cpp(compiler, script, script_object, script_flags, compiler_id, root,
                options.verbose, "driver compile", &dependencies);
    file_action("driver link",
                {compiler.string(), script_object.string(), archive.string(), "-o", driver.string()},
                {script_object, archive}, driver, compiler_id, root, options.verbose);
    // Pass the fresh scan independently of script.o/driver cache identity.
    // A file avoids argv limits and preserves literal filename bytes.
    write_dependency_scan(bootstrap_inputs, dependencies);
    std::vector<std::string> arguments{driver.string(), options.command};
    arguments.insert(arguments.end(), options.arguments.begin(), options.arguments.end());
    return run_process(arguments, root, options.verbose);
}
} // namespace

int main(int argc, char** argv) {
    try {
        const auto options = parse_options(argc, argv);
        if (options.command == "--help") print_help();
        else if (options.command == "--version") std::cout << "cxx M0 (Edition 0)\n";
        else if (options.command == "new") create_project(options.arguments.front());
        else return bootstrap(options, fs::current_path());
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "cxx: " << error.what() << '\n';
    } catch (...) {
        std::cerr << "cxx: unknown bootstrap failure\n";
    }
    return 1;
}
