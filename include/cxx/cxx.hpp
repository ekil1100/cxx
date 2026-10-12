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
enum class TargetKind { executable, static_library };

struct Usage {
    std::string value;
    Visibility visibility;
};

class Build;
class Target {
public:
    Target(const Target&) = delete;
    Target& operator=(const Target&) = delete;
    Target(Target&&) = delete;
    Target& operator=(Target&&) = delete;
    Target& source(std::string path);
    Target& include(std::string path, Visibility visibility = Visibility::private_);
    Target& define(std::string value, Visibility visibility = Visibility::private_);
    Target& link(Target& dependency, Visibility visibility = Visibility::private_);
    Target& cpp_standard(std::string standard);

    const std::string& name() const { return name_; }
    TargetKind kind() const { return kind_; }
    const std::vector<std::string>& sources() const { return sources_; }
    const std::vector<Usage>& includes() const { return includes_; }
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
    std::vector<Usage> includes_;
    std::vector<Usage> defines_;
    std::vector<std::pair<Target*, Visibility>> dependencies_;
    std::string standard_;
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
    const Environment& environment() const { return environment_; }
    const std::vector<std::unique_ptr<Target>>& targets() const { return targets_; }
private:
    Target& add(std::string name, TargetKind kind);
    Environment environment_;
    std::vector<std::unique_ptr<Target>> targets_;
};

} // namespace cxx
