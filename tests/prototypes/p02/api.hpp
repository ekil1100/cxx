#pragma once

// Proposed P02 boundary only. This is not part of the CXX SDK.
#include <cstddef>
#include <memory>
#include <string>

namespace p02 {
class Build;
class Dependency {
    friend class Build;
    Dependency(const Build* owner, std::size_t index) : owner_(owner), index_(index) {}
    const Build* owner_;
    std::size_t index_;
};

class Build {
public:
    explicit Build(std::string variant);
    ~Build();
    Build(const Build&) = delete;
    Build& operator=(const Build&) = delete;
    Dependency dependency(std::string name, int major);
    void link(std::string target, Dependency dependency, std::string exported_target);
    const std::string& variant() const;

    // Internal fixture access, not a proposed public model API.
    struct State;
    const State& finish();
private:
    std::unique_ptr<State> state_;
};

void build(Build&);
int declaration_calls();
} // namespace p02
