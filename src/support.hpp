#pragma once

#include <version>

// Internal-only requirements. The public SDK and driver entry header remain C++17-compatible.
#if __cplusplus <= 202002L
#error "CXX internals require C++23; user build scripts may use C++17 or newer"
#elif !defined(__cpp_lib_ranges_contains) || __cpp_lib_ranges_contains < 202207L
#error "CXX requires std::ranges::contains; select a newer C++ standard library"
#elif !defined(__cpp_lib_string_contains) || __cpp_lib_string_contains < 202011L
#error "CXX requires std::string::contains; select a newer C++ standard library"
#endif

#include <filesystem>
#include <stdexcept>
#include <string>
#include <vector>

namespace cxx::detail {
namespace fs = std::filesystem;

struct Error : std::runtime_error {
    using std::runtime_error::runtime_error;
};

std::string read_file(const fs::path& path);
void write_atomic(const fs::path& path, const std::string& contents);
std::string hash_file(const fs::path& path);
std::string fingerprint(const std::vector<std::string>& fields);
std::string environment_fingerprint();
fs::path resolve_program(const std::string& name);
std::string quote_cpp(const std::string& value);
int run_process(const std::vector<std::string>& args, const fs::path& cwd, bool verbose = false);

// Install paths reject symlinks at every existing component. Check the entire
// plan before writing, then recheck each destination when publishing a file.
void validate_install_path(const fs::path& path, bool directory = false);
void install_file_atomic(const fs::path& source, const fs::path& destination);

// The lock is owned by the launcher and held while its driver child runs.
class ProjectLock {
public:
    explicit ProjectLock(const fs::path& path);
    ~ProjectLock();
    ProjectLock(const ProjectLock&) = delete;
    ProjectLock& operator=(const ProjectLock&) = delete;
private:
    int fd_ = -1;
};

// Accumulates discovered paths across scans, including cache hits. Incomplete
// scans retain known paths but cannot prove that installation protects all inputs.
struct DependencyScan {
    std::vector<fs::path> paths;
    bool complete = true;
};

// Private launcher-to-Runtime handoff under the project lock. NUL-delimited
// filenames preserve whitespace and newlines without exposing C++23 SDK types.
void write_dependency_scan(const fs::path& path, const DependencyScan& scan);
DependencyScan read_dependency_scan(const fs::path& path);

// Scan on every call. Hash preprocessed output and raw dependencies, then compile
// the original source on a miss. Re-scan before publishing to detect input changes.
// Keys are local-only and include paths, flags, environment and tool identity.
bool compile_cpp(const fs::path& compiler, const fs::path& source,
                 const fs::path& output, const std::vector<std::string>& flags,
                 const std::string& tool_identity, const fs::path& cwd,
                 bool verbose, const std::string& label = "compile",
                 DependencyScan* discovered_inputs = nullptr);

// Arguments naming output exactly are replaced with a temporary output path.
// Inputs are content-hashed before and after execution. Failed or changed-input
// actions preserve previous outputs and keys. Uncacheable actions always execute.
bool file_action(const std::string& label, const std::vector<std::string>& args,
                 const std::vector<fs::path>& inputs, const fs::path& output,
                 const std::string& tool_identity, const fs::path& cwd, bool verbose,
                 bool cacheable = true);
} // namespace cxx::detail
