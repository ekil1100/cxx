// Isolated P02 experiment: no production Build API or solver implementation.
#include "api.hpp"
#include "support.hpp"
#include "toml.hpp"

#include <algorithm>
#include <iostream>
#include <map>
#include <set>
#include <sstream>
#include <tuple>
#include <unistd.h>

namespace p02 {
using cxx::detail::Error;
namespace fs = std::filesystem;
using cxx::detail::fingerprint;
using cxx::detail::read_file;
using cxx::detail::write_atomic;

void check(bool condition, const std::string& message) {
    if (!condition) throw Error(message);
}

struct Requirement { std::string name; int major; };
struct Link { std::string target; std::size_t index; std::string exported; };
struct Build::State {
    std::string variant;
    std::vector<Requirement> requirements;
    std::vector<Link> links;
    bool closed = false;
};

Build::Build(std::string variant) : state_(std::make_unique<State>()) {
    state_->variant = std::move(variant);
}
Build::~Build() = default;
const std::string& Build::variant() const { return state_->variant; }
Dependency Build::dependency(std::string name, int major) {
    check(!state_->closed, "Declaration is closed");
    const auto index = state_->requirements.size();
    state_->requirements.push_back({std::move(name), major});
    return Dependency(this, index);
}
void Build::link(std::string target, Dependency dependency, std::string exported) {
    check(!state_->closed, "Declaration is closed");
    check(dependency.owner_ == this, "Dependency belongs to another Build");
    state_->links.push_back({std::move(target), dependency.index_, std::move(exported)});
}
const Build::State& Build::finish() {
    check(!state_->closed, "Declaration is closed");
    state_->closed = true;
    return *state_;
}

std::string encode(const toml::table& table) {
    std::ostringstream out;
    out << toml::toml_formatter{table} << '\n';
    return out.str();
}
const toml::table& table_at(const toml::table& table, std::string_view key) {
    const auto* value = table[key].as_table();
    check(value != nullptr, "Missing table: " + std::string(key));
    return *value;
}
const toml::array& array_at(const toml::table& table, std::string_view key) {
    const auto* value = table[key].as_array();
    check(value != nullptr, "Missing array: " + std::string(key));
    return *value;
}
std::string text_at(const toml::table& table, std::string_view key) {
    const auto value = table[key].value<std::string>();
    check(value.has_value(), "Missing string: " + std::string(key));
    return *value;
}
int number_at(const toml::table& table, std::string_view key) {
    const auto value = table[key].value<int>();
    check(value.has_value(), "Missing integer: " + std::string(key));
    return *value;
}

toml::table collect(Build& build) {
    const auto& state = build.finish();
    toml::table roots;
    for (const auto& requirement : state.requirements) {
        if (roots.contains(requirement.name)) {
            check(number_at(roots, requirement.name) == requirement.major, "Conflicting root requirements");
        }
        roots.insert_or_assign(requirement.name, requirement.major);
    }
    // Copy values out of the builder. No runtime handles enter persisted state.
    std::set<std::tuple<std::string, std::string, std::string>> links;
    for (const auto& link : state.links) {
        links.emplace(link.target, state.requirements.at(link.index).name, link.exported);
    }
    toml::array declarations;
    for (const auto& [target, package, exported] : links) {
        declarations.push_back(toml::table{{"target", target}, {"package", package}, {"export", exported}});
    }
    check(state.requirements.size() >= 257, "Handle growth fixture did not run");
    try {
        build.dependency("late", 1);
        throw Error("Closed declaration accepted a dependency");
    } catch (const Error& error) {
        check(std::string(error.what()) == "Declaration is closed", error.what());
    }
    return toml::table{{"roots", std::move(roots)}, {"links", std::move(declarations)}};
}

// Only simple three-component numeric fixture versions. No ranges or backtracking.
std::tuple<int, int, int> version(const std::string& value) {
    std::istringstream in(value);
    int major, minor, patch;
    char first, second;
    check(bool(in >> major >> first >> minor >> second >> patch) && first == '.' && second == '.' &&
          in.peek() == std::char_traits<char>::eof(), "Invalid fixture version: " + value);
    return {major, minor, patch};
}

int metadata_reads = 0;
class Registry {
public:
    explicit Registry(const fs::path& path) : metadata_(toml::parse_file(path.string())) { ++metadata_reads; }

    toml::table resolve(const toml::table& roots, const toml::table& domain,
                        const toml::table& locked) const {
        toml::table selected;
        std::set<std::string> active;
        auto visit = [&](this auto&& self, const std::string& name, int major) -> void {
            check(!active.contains(name), "Metadata cycle at " + name);
            if (selected.contains(name)) {
                check(number_at(table_at(selected, name), "major") == major,
                      "Prototype constraint conflict at " + name + " (no backtracking)");
                return;
            }
            const toml::table* old = locked[name].as_table();
            if (old) check(number_at(*old, "major") == major, "Locked choice requires explicit update: " + name);
            const toml::table* chosen = nullptr;
            for (const auto& node : array_at(metadata_, "packages")) {
                const auto& item = *node.as_table(); // Trusted fixture schema.
                if (text_at(item, "name") != name || number_at(item, "major") != major) continue;
                const auto candidate = text_at(item, "version");
                if (old && candidate != text_at(*old, "version")) continue;
                if (!chosen || version(candidate) > version(text_at(*chosen, "version"))) chosen = &item;
            }
            check(chosen != nullptr, "Missing compatible metadata: " + name);
            toml::table dependencies;
            for (const auto& node : array_at(*chosen, "dependencies")) {
                const auto& dep = *node.as_table();
                bool matches = true;
                for (const auto* field : {"platform", "profile", "role"}) {
                    if (dep.contains(field) && text_at(dep, field) != text_at(domain, field)) matches = false;
                }
                if (matches) dependencies.insert_or_assign(text_at(dep, "name"), number_at(dep, "major"));
            }
            const auto exact = text_at(*chosen, "version");
            toml::table record{{"version", exact}, {"major", major},
                {"source", "fixture:registry/" + name + "/" + exact},
                {"metadata_digest", fingerprint({"p02-metadata-v1", encode(*chosen)})},
                {"exports", array_at(*chosen, "exports")}, {"dependencies", dependencies}};
            if (old) check(*old == record, "Locked metadata identity changed: " + name);
            active.insert(name);
            for (const auto& [key, value] : dependencies) self(std::string(key.str()), *value.value<int>());
            active.erase(name);
            selected.insert(name, std::move(record));
        };
        for (const auto& [name, constraint] : roots) visit(std::string(name.str()), number_at(roots, name.str()));
        return selected;
    }
private:
    toml::table metadata_;
};

toml::table fixture_toolchain() {
    return toml::table{{"name", "p02-fixture-toolchain"}, {"version", "1"}, {"source", "fixture:toolchain"},
            {"digest", fingerprint({"p02-synthetic-toolchain-1"})}};
}
std::string domain_key(const toml::table& domain) {
    return fingerprint({"p02-domain-v1", text_at(domain, "platform"),
                        text_at(domain, "profile"), text_at(domain, "role")});
}

// A value-owned frozen model for this experiment, represented as a private DTO.
toml::table resolve_set(const Registry& registry, const toml::table& declaration,
                        const toml::table& domain, const toml::table& old_packages) {
    const auto packages = registry.resolve(table_at(declaration, "roots"), domain, old_packages);
    for (const auto& node : array_at(declaration, "links")) {
        check(node.is_table(), "Invalid locked link declaration");
        const auto& link = *node.as_table();
        const auto& exports = array_at(table_at(packages, text_at(link, "package")), "exports");
        check(std::ranges::any_of(exports, [&](const auto& item) {
            return item.template value<std::string>() == text_at(link, "export");
        }), "Missing package export: " + text_at(link, "export"));
    }
    return toml::table{{"domain", domain}, {"declaration", declaration},
            {"declaration_fingerprint", fingerprint({"p02-declaration-v1", encode(domain), encode(declaration)})},
            {"toolchain", fixture_toolchain()}, {"packages", packages}};
}
void validate_lock(const toml::table& lock, const Registry& registry) {
    check(number_at(lock, "lock_format") == 1, "Unknown prototype lock format");
    for (const auto& [key, node] : table_at(lock, "resolution_sets")) {
        const auto* set = node.as_table();
        check(set != nullptr, "Invalid resolution set");
        const auto& domain = table_at(*set, "domain");
        check(key.str() == domain_key(domain), "Resolution domain key mismatch");
        const auto expected = resolve_set(registry, table_at(*set, "declaration"), domain, table_at(*set, "packages"));
        check(*set == expected, "Incomplete or inconsistent locked resolution");
    }
}

void checkpoint(const std::string& requested, const std::string& phase) {
    if (requested != phase) return;
    std::cout << "checkpoint " << phase << '\n' << std::flush;
    std::string command;
    check(bool(std::getline(std::cin, command)) && command == "continue", "Checkpoint abandoned");
}
void publish(const fs::path& path, const toml::table& candidate, const Registry& registry,
             const std::string& pause) {
    // Staging is in the destination directory. Support's writer has no fsync guarantee.
    const fs::path staged = path.string() + ".candidate." + std::to_string(getpid());
    write_atomic(staged, encode(candidate));
    validate_lock(toml::parse_file(staged.string()), registry);
    checkpoint(pause, "before-publish");
    fs::rename(staged, path);
    checkpoint(pause, "after-publish");
}
} // namespace p02

int main(int argc, char** argv) {
    using namespace p02;
    try {
        if (argc == 4 && std::string(argv[1]) == "inspect") {
            const Registry registry(argv[3]);
            const auto lock = toml::parse_file(argv[2]);
            validate_lock(lock, registry);
            std::cout << toml::json_formatter{lock} << '\n';
            return 0;
        }
        check(argc == 9,
              "Usage: p02 <lock> <registry> <platform> <profile> <host|target> <variant> <build|locked> <pause|none>");
        const fs::path path = argv[1];
        const std::string mode = argv[7];
        check(mode == "build" || mode == "locked", "Unknown prototype mode");
        check(std::string(argv[5]) == "host" || std::string(argv[5]) == "target", "Unknown package role");
        const toml::table domain{{"platform", argv[3]}, {"profile", argv[4]}, {"role", argv[5]}};
        const auto declaration = [&] {
            Build builder(argv[6]);
            build(builder);
            check(declaration_calls() == 1, "Expected exactly one declaration invocation");
            // Exercise the concrete cross-builder handle failure before destroying the builder.
            Build foreign("base");
            try {
                builder.link("bad", foreign.dependency("widget", 1), "widget::widget");
                throw Error("Foreign dependency was accepted");
            } catch (const Error& error) {
                check(std::string(error.what()) == "Dependency belongs to another Build", error.what());
            }
            return collect(builder);
        }();
        check(metadata_reads == 0, "Metadata was read during declaration");
        std::cout << "declaration calls=" << declaration_calls()
                  << " handles=stable metadata_reads=" << metadata_reads << " builder=destroyed\n" << std::flush;
        const Registry registry(argv[2]); // Metadata access begins only after declaration collection.
        std::unique_ptr<cxx::detail::ProjectLock> transaction;
        if (mode == "build") transaction = std::make_unique<cxx::detail::ProjectLock>(path.parent_path() / "transaction.lock");
        // Read after acquiring exclusivity. A rejected contender retries from a fresh snapshot.
        check(mode != "locked" || fs::exists(path), "Locked mode requires an existing lock");
        const auto original = fs::exists(path) ? toml::parse_file(path.string()) :
            toml::table{{"lock_format", 1}, {"resolution_sets", toml::table{}}};
        validate_lock(original, registry);
        auto candidate = original;
        auto& sets = *candidate["resolution_sets"].as_table();
        const auto key = domain_key(domain);
        const auto* previous = sets[key].as_table();
        check(mode != "locked" || previous != nullptr, "Locked mode is missing the requested domain");
        const auto resolved = resolve_set(registry, declaration, domain,
                                         previous ? table_at(*previous, "packages") : toml::table{});
        sets.insert_or_assign(key, resolved);
        validate_lock(candidate, registry);
        check(mode != "locked" || candidate == original, "Locked mode declaration mismatch");
        if (mode == "build" && candidate != original) publish(path, candidate, registry, argv[8]);
        check(declaration_calls() == 1, "Resolver reran the declaration");
        std::cout << "resolved calls=" << declaration_calls() << " sets=" << sets.size() << " packages="
                  << table_at(resolved, "packages").size() << " mode=" << mode << '\n';
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "p02: error: " << error.what() << '\n';
        return 1;
    }
}
