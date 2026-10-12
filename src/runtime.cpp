#include "runtime.hpp"
#include "support.hpp"

#include <algorithm>
#include <iostream>
#include <map>
#include <set>
#include <utility>

namespace cxx {
namespace {

void require_standard(const std::string& standard) {
    if (standard != "17" && standard != "20" && standard != "23" && standard != "26") {
        throw detail::Error("Unsupported C++ standard '" + standard + "'; expected 17, 20, 23 or 26");
    }
}

void require_visibility(Visibility visibility) {
    switch (visibility) {
    case Visibility::private_:
    case Visibility::public_:
    case Visibility::interface_:
        return;
    }
    throw detail::Error("Invalid usage visibility");
}

bool safe_target_name(const std::string& name) {
    const auto initial = [](char c) {
        return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') || c == '_';
    };
    if (name.empty() || !initial(name.front())) return false;
    return std::ranges::all_of(name, [&](char c) {
        return initial(c) || (c >= '0' && c <= '9') || c == '-';
    });
}

std::string target_name_key(std::string name) {
    // Artifact directories must be distinct on case-insensitive filesystems too.
    for (char& c : name) {
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + ('a' - 'A'));
    }
    return name;
}

void require_text(const std::string& value, const std::string& label) {
    if (value.empty() || value.contains('\0')) {
        throw detail::Error("Invalid " + label + ": expected nonempty text without NUL bytes");
    }
}

} // namespace

Target& Target::source(std::string path) {
    require_text(path, "source path");
    sources_.push_back(std::move(path));
    return *this;
}

Target& Target::include(std::string path, Visibility visibility) {
    require_text(path, "include path");
    require_visibility(visibility);
    includes_.push_back({std::move(path), visibility});
    return *this;
}

Target& Target::define(std::string value, Visibility visibility) {
    require_text(value, "definition");
    require_visibility(visibility);
    defines_.push_back({std::move(value), visibility});
    return *this;
}

Target& Target::link(Target& dependency, Visibility visibility) {
    require_visibility(visibility);
    if (owner_ != dependency.owner_) {
        throw detail::Error("Target '" + name_ + "' references target '" + dependency.name_ +
                            "' from another Build");
    }
    if (dependency.kind_ == TargetKind::executable) {
        throw detail::Error("Target '" + name_ + "' cannot depend on executable '" + dependency.name_ + "'");
    }
    dependencies_.emplace_back(&dependency, visibility);
    return *this;
}

Target& Target::cpp_standard(std::string standard) {
    require_standard(standard);
    standard_ = std::move(standard);
    return *this;
}

Target& Build::add(std::string name, TargetKind kind) {
    if (!safe_target_name(name)) {
        throw detail::Error("Invalid target name '" + name + "'; expected [A-Za-z_][A-Za-z0-9_-]*");
    }
    const auto key = target_name_key(name);
    if (std::ranges::any_of(targets_, [&](const auto& target) {
            return target_name_key(target->name()) == key;
        })) {
        throw detail::Error("Duplicate target name (ASCII case-insensitive): " + name);
    }
    targets_.push_back(std::unique_ptr<Target>(new Target(this, std::move(name), kind)));
    return *targets_.back();
}

Target& Build::executable(std::string name) {
    return add(std::move(name), TargetKind::executable);
}

Target& Build::static_library(std::string name) {
    return add(std::move(name), TargetKind::static_library);
}

namespace detail {
namespace {

struct Command {
    bool run = false;
    bool verbose = false;
    std::vector<std::string> targets;
    std::vector<std::string> arguments;
};

Command parse_command(int argc, char** argv) {
    if (argc < 2 || !argv || !argv[1]) {
        throw Error("Usage: driver build [target...] [-v] | driver run [target] [-v] [-- args...]");
    }
    Command command;
    const std::string action = argv[1];
    if (action == "run") command.run = true;
    else if (action != "build") throw Error("Unknown command '" + action + "'; expected build or run");
    bool application_arguments = false;
    for (int i = 2; i < argc; ++i) {
        if (!argv[i]) throw Error("Invalid null command argument");
        const std::string argument = argv[i];
        if (application_arguments) command.arguments.push_back(argument);
        else if (argument == "-v") command.verbose = true;
        else if (argument == "--" && command.run) application_arguments = true;
        else if (!safe_target_name(argument)) throw Error("Invalid option or target: " + argument);
        else command.targets.push_back(argument);
    }
    if (command.run && command.targets.size() > 1) {
        throw Error("run accepts at most one target; place application arguments after --");
    }
    return command;
}

fs::path project_path(const fs::path& root, const std::string& path) {
    // Let the filesystem resolve symlinks before '..'. Preserve source spelling
    // as well: canonicalizing a source symlink changes quoted include lookup.
    return root / fs::path(path);
}

Environment resolved_environment(Environment environment) {
    require_standard(environment.cpp_standard);
    require_text(environment.project_root, "project root");
    environment.project_root = fs::canonical(environment.project_root).string();
    if (!fs::is_directory(environment.project_root)) {
        throw Error("Project root is not a directory: " + environment.project_root);
    }
    // Keep compiler driver symlink names (clang++ selects C++ linking behavior).
    require_text(environment.compiler, "compiler path");
    require_text(environment.archiver, "archiver path");
    environment.compiler = resolve_program(environment.compiler).string();
    environment.archiver = resolve_program(environment.archiver).string();
    return environment;
}

// Validate every declaration, including targets outside the selected closure.
std::vector<const Target*> validate_graph(const Build& build) {
    std::set<const Target*> members;
    std::set<std::string> names;
    const fs::path root = build.environment().project_root;
    for (const auto& target : build.targets()) {
        members.insert(target.get());
        if (!safe_target_name(target->name())) throw Error("Invalid target name: " + target->name());
        if (!names.insert(target_name_key(target->name())).second) {
            throw Error("Duplicate target name (ASCII case-insensitive): " + target->name());
        }
        if (!target->standard().empty()) require_standard(target->standard());
        if (target->sources().empty()) throw Error("Target '" + target->name() + "' has no sources");
        for (const auto& source : target->sources()) {
            const auto path = project_path(root, source);
            if (!fs::is_regular_file(path)) {
                throw Error("Missing source for target '" + target->name() + "': " + path.string());
            }
        }
    }
    for (const auto& target : build.targets()) {
        for (const auto& edge : target->dependencies()) {
            if (!members.contains(edge.first)) {
                throw Error("Target '" + target->name() + "' references a target outside this Build");
            }
            if (edge.first->kind() != TargetKind::static_library) {
                throw Error("Target '" + target->name() + "' cannot depend on executable '" + edge.first->name() + "'");
            }
        }
    }

    std::map<const Target*, int> state;
    std::vector<const Target*> stack;
    std::vector<const Target*> order;
    auto visit = [&](this auto&& self, const Target* target) -> void {
        if (state[target] == 2) return;
        if (state[target] == 1) {
            std::string cycle;
            const auto start = std::ranges::find(stack, target);
            for (auto it = start; it != stack.end(); ++it) cycle += (*it)->name() + " -> ";
            throw Error("Dependency cycle: " + cycle + target->name());
        }
        state[target] = 1;
        stack.push_back(target);
        for (const auto& edge : target->dependencies()) self(edge.first);
        stack.pop_back();
        state[target] = 2;
        order.push_back(target);
    };
    for (const auto& target : build.targets()) visit(target.get());
    return order;
}

std::vector<const Target*> select_targets(const Build& build, const Command& command) {
    std::vector<const Target*> selected;
    for (const auto& name : command.targets) {
        const auto found = std::ranges::find(build.targets(), name, &Target::name);
        if (found == build.targets().end()) throw Error("Unknown target: " + name);
        if (!std::ranges::contains(selected, found->get())) {
            selected.push_back(found->get());
        }
    }
    if (command.targets.empty()) {
        for (const auto& target : build.targets()) {
            if (!command.run || target->kind() == TargetKind::executable) selected.push_back(target.get());
        }
        if (command.run && selected.size() != 1) {
            throw Error("run requires exactly one executable when no target is specified; found " +
                        std::to_string(selected.size()) + ". Use run <target>.");
        }
    }
    if (command.run && selected.front()->kind() != TargetKind::executable) {
        throw Error("Target '" + selected.front()->name() + "' is not an executable");
    }
    return selected;
}

void add_closure(const Target* target, std::set<const Target*>& closure) {
    if (!closure.insert(target).second) return;
    for (const auto& edge : target->dependencies()) add_closure(edge.first, closure);
}

struct CompileUsage {
    std::vector<std::string> includes;
    std::vector<std::string> defines;
};

void append_unique(std::vector<std::string>& values, const std::string& value) {
    if (!std::ranges::contains(values, value)) values.push_back(value);
}

void merge_usage(CompileUsage& into, const CompileUsage& from) {
    for (const auto& value : from.includes) append_unique(into.includes, value);
    for (const auto& value : from.defines) append_unique(into.defines, value);
}

CompileUsage own_usage(const Target& target, bool exported) {
    CompileUsage result;
    const auto visible = [exported](Visibility visibility) {
        return exported ? visibility != Visibility::private_ : visibility != Visibility::interface_;
    };
    for (const auto& usage : target.includes()) {
        if (visible(usage.visibility)) append_unique(result.includes, usage.value);
    }
    for (const auto& usage : target.defines()) {
        if (visible(usage.visibility)) append_unique(result.defines, usage.value);
    }
    return result;
}

// Usage propagation is independent of archive ordering and link-only dependencies.
std::map<const Target*, CompileUsage> compile_usages(const std::vector<const Target*>& order) {
    std::map<const Target*, CompileUsage> exported;
    std::map<const Target*, CompileUsage> local;
    for (const auto* target : order) {
        auto own = own_usage(*target, false);
        auto interface = own_usage(*target, true);
        for (const auto& edge : target->dependencies()) {
            const auto& dependency = exported.at(edge.first);
            if (edge.second != Visibility::interface_) merge_usage(own, dependency);
            if (edge.second != Visibility::private_) merge_usage(interface, dependency);
        }
        local.emplace(target, std::move(own));
        exported.emplace(target, std::move(interface));
    }
    return local;
}

fs::path artifact(const fs::path& root, const Target& target) {
    const auto directory = root / ".cxx" / "build" / target.name();
    return directory / (target.kind() == TargetKind::executable ? target.name() : "lib" + target.name() + ".a");
}

std::vector<const Target*> link_libraries(const Target& executable) {
    std::vector<const Target*> libraries;
    std::set<const Target*> seen;
    auto collect = [&](this auto&& self, const Target* target) -> void {
        if (!seen.insert(target).second) return;
        libraries.push_back(target);
        // Static archives retain unresolved references to private dependencies.
        for (const auto& edge : target->dependencies()) self(edge.first);
    };
    for (const auto& edge : executable.dependencies()) {
        if (edge.second != Visibility::interface_) collect(edge.first);
    }

    // Kahn ordering emits each consumer before all its dependencies. DFS preorder
    // alone would put a shared diamond dependency ahead of its second consumer.
    std::map<const Target*, std::size_t> consumers;
    for (const auto* library : libraries) consumers.emplace(library, 0);
    for (const auto* library : libraries) {
        for (const auto& edge : library->dependencies()) ++consumers.at(edge.first);
    }
    std::vector<const Target*> ready;
    for (const auto* library : libraries) {
        if (consumers.at(library) == 0) ready.push_back(library);
    }
    for (std::size_t i = 0; i < ready.size(); ++i) {
        for (const auto& edge : ready[i]->dependencies()) {
            if (--consumers.at(edge.first) == 0) ready.push_back(edge.first);
        }
    }
    if (ready.size() != libraries.size()) throw Error("Dependency cycle in static library link order");
    return ready;
}

enum class ActionKind { compile, archive, link };
struct Action {
    ActionKind kind;
    std::vector<std::string> arguments;
    std::vector<fs::path> inputs;
    fs::path output;
    std::vector<std::size_t> predecessors;
};

// The plan is stored separately from execution. Producers precede consumers in
// this sequential M0 schedule; M1 can schedule the same explicit dependency edges.
void append_action(std::vector<Action>& graph, std::map<fs::path, std::size_t>& producers,
                   Action action) {
    if (producers.contains(action.output)) throw Error("Multiple writers for output: " + action.output.string());
    for (const auto& input : action.inputs) {
        const auto producer = producers.find(input);
        if (producer != producers.end()) action.predecessors.push_back(producer->second);
        else if (!fs::is_regular_file(input)) throw Error("Missing action input: " + input.string());
    }
    producers.emplace(action.output, graph.size());
    graph.push_back(std::move(action));
}

void build_targets(const Build& build, const std::vector<const Target*>& order,
                   const std::vector<const Target*>& selected, bool verbose) {
    std::set<const Target*> closure;
    for (const auto* target : selected) add_closure(target, closure);
    const auto usages = compile_usages(order);
    const auto& environment = build.environment();
    const fs::path root = environment.project_root;
    // Content identities also invalidate actions when tools change in place.
    const auto compiler_identity = closure.empty() ? std::string{} : hash_file(environment.compiler);
    const bool has_archive = std::ranges::any_of(closure, [](const Target* target) {
        return target->kind() == TargetKind::static_library;
    });
    const auto archiver_identity = has_archive ? hash_file(environment.archiver) : std::string{};
    std::vector<Action> graph;
    std::map<fs::path, std::size_t> producers;
    for (const auto* target : order) {
        if (!closure.contains(target)) continue;
        const auto output = artifact(root, *target);
        const auto& standard = target->standard().empty() ? environment.cpp_standard : target->standard();
        std::vector<std::string> flags{"-std=c++" + standard};
        for (const auto& include : usages.at(target).includes) {
            flags.push_back("-I" + project_path(root, include).string());
        }
        for (const auto& define : usages.at(target).defines) flags.push_back("-D" + define);
        std::vector<fs::path> inputs;
        std::set<fs::path> sources;
        for (const auto& source : target->sources()) {
            const auto path = project_path(root, source);
            if (!sources.insert(path).second) continue;
            const auto object = output.parent_path() / ".objects" / (fingerprint({path.string()}) + ".o");
            append_action(graph, producers, {ActionKind::compile, flags, {path}, object, {}});
            inputs.push_back(object);
        }
        if (target->kind() == TargetKind::static_library) {
            std::vector<std::string> args{environment.archiver, "rcs", output.string()};
            for (const auto& input : inputs) args.push_back(input.string());
            append_action(graph, producers, {ActionKind::archive, std::move(args), std::move(inputs), output, {}});
        } else {
            std::vector<std::string> args{environment.compiler, "-std=c++" + standard};
            for (const auto& input : inputs) args.push_back(input.string());
            for (const auto* library : link_libraries(*target)) {
                const auto archive = artifact(root, *library);
                args.push_back(archive.string());
                inputs.push_back(archive);
            }
            args.insert(args.end(), {"-o", output.string()});
            append_action(graph, producers, {ActionKind::link, std::move(args), std::move(inputs), output, {}});
        }
    }
    std::size_t compiled = 0, archived = 0, linked = 0;
    for (std::size_t index = 0; index < graph.size(); ++index) {
        const auto& action = graph[index];
        for (const auto predecessor : action.predecessors) {
            if (predecessor >= index) throw Error("Invalid action dependency order");
        }
        if (action.kind == ActionKind::compile) {
            if (compile_cpp(environment.compiler, action.inputs.front(), action.output,
                            action.arguments, compiler_identity, root, verbose)) ++compiled;
        } else if (action.kind == ActionKind::archive) {
            if (file_action("archive", action.arguments, action.inputs, action.output,
                            archiver_identity, root, verbose)) ++archived;
        } else {
            if (file_action("link", action.arguments, action.inputs, action.output,
                            compiler_identity, root, verbose)) ++linked;
        }
    }
    std::cerr << "[build] targets=" << closure.size() << " compiled=" << compiled
              << " archived=" << archived << " linked=" << linked << '\n';
}

} // namespace

int driver_main(int argc, char** argv, void (*declaration)(cxx::Build&), Environment environment) {
    try {
        const auto command = parse_command(argc, argv);
        if (!declaration) throw Error("Missing build declaration function");
        Build build(resolved_environment(std::move(environment)));
        declaration(build);
        const auto order = validate_graph(build);
        const auto selected = select_targets(build, command);
        build_targets(build, order, selected, command.verbose);
        if (command.run) {
            const auto& root = build.environment().project_root;
            std::vector<std::string> args{artifact(root, *selected.front()).string()};
            args.insert(args.end(), command.arguments.begin(), command.arguments.end());
            return run_process(args, root, command.verbose);
        }
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "cxx: error: " << error.what() << '\n';
    } catch (...) {
        std::cerr << "cxx: error: unknown exception in build declaration or runtime\n";
    }
    return 1;
}

} // namespace detail
} // namespace cxx
