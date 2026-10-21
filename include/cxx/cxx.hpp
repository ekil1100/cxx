#pragma once

#include <memory>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

namespace cxx {

struct Environment {
    std::string compiler;
    std::string archiver;
    std::string cpp_standard;
    std::string project_root;
};

enum class Visibility { private_, public_, interface_ };
enum class TargetKind { executable, static_library, shared_library, object_library };
enum class Optimization { default_, none, speed };

struct Usage {
    std::string value;
    Visibility visibility;
};

class Build;
class CustomAction;

// A producer handle remains valid for the lifetime of its owning Build.
class GeneratedFile {
public:
    const std::string& path() const { return path_; }
    const CustomAction& producer() const { return *producer_; }
private:
    friend class CustomAction;
    friend class Target;
    friend class Build;
    GeneratedFile(Build* owner, const CustomAction* producer, std::string path)
        : owner_(owner), producer_(producer), path_(std::move(path)) {}
    Build* owner_;
    const CustomAction* producer_;
    std::string path_;
};

class CustomAction {
public:
    CustomAction(const CustomAction&) = delete;
    CustomAction& operator=(const CustomAction&) = delete;
    CustomAction(CustomAction&&) = delete;
    CustomAction& operator=(CustomAction&&) = delete;
    GeneratedFile output(std::string filename);
    CustomAction& command(std::vector<std::string> argv);
    CustomAction& input(std::string path);
    CustomAction& input(const GeneratedFile& file);
    // Opt in only when all scripts/data read by the tool are declared inputs.
    CustomAction& cacheable(bool enabled = true);

    const std::string& name() const { return name_; }
    const std::string& output_path() const { return output_; }
    const std::vector<std::string>& arguments() const { return arguments_; }
    const std::vector<std::string>& inputs() const { return inputs_; }
    const std::vector<GeneratedFile>& generated_inputs() const { return generated_inputs_; }
    bool is_cacheable() const { return cacheable_; }
private:
    friend class Build;
    CustomAction(Build* owner, std::string name) : owner_(owner), name_(std::move(name)) {}
    Build* owner_;
    std::string name_;
    std::string output_;
    std::vector<std::string> arguments_;
    std::vector<std::string> inputs_;
    std::vector<GeneratedFile> generated_inputs_;
    bool cacheable_ = false;
};

class Target {
public:
    Target(const Target&) = delete;
    Target& operator=(const Target&) = delete;
    Target(Target&&) = delete;
    Target& operator=(Target&&) = delete;
    Target& source(std::string path);
    Target& source(const GeneratedFile& file);
    Target& depends_on(const GeneratedFile& file);
    Target& include(std::string path, Visibility visibility = Visibility::private_);
    Target& system_include(std::string path, Visibility visibility = Visibility::private_);
    Target& optimization(Optimization level);
    Target& warnings(bool enabled = true);
    Target& define(std::string value, Visibility visibility = Visibility::private_);
    Target& link(Target& dependency, Visibility visibility = Visibility::private_);
    Target& cpp_standard(std::string standard);

    const std::string& name() const { return name_; }
    TargetKind kind() const { return kind_; }
    const std::vector<std::string>& sources() const { return sources_; }
    const std::vector<GeneratedFile>& generated_sources() const { return generated_sources_; }
    const std::vector<GeneratedFile>& generation_dependencies() const { return generation_dependencies_; }
    const std::vector<Usage>& includes() const { return includes_; }
    const std::vector<Usage>& system_includes() const { return system_includes_; }
    Optimization optimization() const { return optimization_; }
    bool warnings_enabled() const { return warnings_; }
    const std::vector<Usage>& defines() const { return defines_; }
    const std::vector<std::pair<Target*, Visibility>>& dependencies() const { return dependencies_; }
    const std::string& standard() const { return standard_; }
private:
    friend class Build;
    Target(Build* owner, std::string name, TargetKind kind)
        : owner_(owner), name_(std::move(name)), kind_(kind) {}
    Build* owner_;
    std::string name_;
    TargetKind kind_;
    std::vector<std::string> sources_;
    std::vector<GeneratedFile> generated_sources_;
    std::vector<GeneratedFile> generation_dependencies_;
    std::vector<Usage> includes_;
    std::vector<Usage> system_includes_;
    Optimization optimization_ = Optimization::default_;
    bool warnings_ = false;
    std::vector<Usage> defines_;
    std::vector<std::pair<Target*, Visibility>> dependencies_;
    std::string standard_;
};

struct Test {
    std::string name;
    const Target* executable;
    std::vector<std::string> arguments;
};

struct InstallFile {
    std::string source;
    std::string destination;
    const CustomAction* producer;
};

class Build {
public:
    explicit Build(Environment environment) : environment_(std::move(environment)) {}
    Build(const Build&) = delete;
    Build& operator=(const Build&) = delete;
    Build(Build&&) = delete;
    Build& operator=(Build&&) = delete;
    Target& executable(std::string name);
    Target& static_library(std::string name);
    Target& shared_library(std::string name);
    Target& object_library(std::string name);
    CustomAction& custom_action(std::string name);
    void test(std::string name, Target& executable, std::vector<std::string> args = {});
    void install(Target& target);
    // Destination is a complete prefix-relative filename, not a directory.
    void install_file(std::string source, std::string destination);
    void install_file(const GeneratedFile& source, std::string destination);
    const std::vector<Test>& tests() const { return tests_; }
    const std::vector<const Target*>& installs() const { return installs_; }
    const std::vector<InstallFile>& install_files() const { return install_files_; }
    const Environment& environment() const { return environment_; }
    const std::vector<std::unique_ptr<Target>>& targets() const { return targets_; }
    const std::vector<std::unique_ptr<CustomAction>>& custom_actions() const { return custom_actions_; }
private:
    Target& add(std::string name, TargetKind kind);
    Environment environment_;
    std::vector<std::unique_ptr<Target>> targets_;
    std::vector<std::unique_ptr<CustomAction>> custom_actions_;
    std::vector<Test> tests_;
    std::vector<const Target*> installs_;
    std::vector<InstallFile> install_files_;
};

} // namespace cxx
