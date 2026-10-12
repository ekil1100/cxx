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

// Scan on every call. Hash preprocessed output and raw dependencies, then compile
// the original source on a miss. Re-scan before publishing to detect input changes.
// Keys are local-only and include paths, flags, environment and tool identity.
bool compile_cpp(const fs::path& compiler, const fs::path& source,
                 const fs::path& output, const std::vector<std::string>& flags,
                 const std::string& tool_identity, const fs::path& cwd,
                 bool verbose, const std::string& label = "compile");

// Arguments naming output exactly are replaced with a temporary output path.
// All file inputs are content-hashed. Failed actions preserve previous outputs.
bool file_action(const std::string& label, const std::vector<std::string>& args,
                 const std::vector<fs::path>& inputs, const fs::path& output,
                 const std::string& tool_identity, const fs::path& cwd, bool verbose);
} // namespace cxx::detail
