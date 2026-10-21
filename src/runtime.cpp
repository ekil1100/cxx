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

void require_install_destination(const std::string& destination) {
    require_text(destination, "install destination");
    const detail::fs::path path(destination);
    if (path.is_absolute() || destination.contains('\\') || destination.contains(':') ||
        destination.contains("//")) {
        throw detail::Error("Install destination must be a safe prefix-relative filename: " + destination);
    }
    for (const auto& part : path) {
        if (part.empty() || part == "." || part == "..") {
            throw detail::Error("Install destination must have no empty or dot components: " + destination);
        }
    }
    const auto key = target_name_key(destination);
    if (key == "bin" || key == "lib") {
        throw detail::Error("Install destination conflicts with reserved bin/lib directories: " + destination);
    }
}

} // namespace

Target& Target::source(std::string path) {
    require_text(path, "source path");
    sources_.push_back(std::move(path));
    return *this;
}

Target& Target::source(const GeneratedFile& file) {
    depends_on(file);
    generated_sources_.push_back(file);
    sources_.push_back(file.path());
    return *this;
}

Target& Target::depends_on(const GeneratedFile& file) {
    if (owner_ != file.owner_) {
        throw detail::Error("Target '" + name_ + "' references a generated file from another Build");
    }
    generation_dependencies_.push_back(file);
    return *this;
}

GeneratedFile CustomAction::output(std::string filename) {
    require_text(filename, "generated filename");
    const detail::fs::path path(filename);
    if (path.is_absolute() || filename == "." || filename == ".." ||
        filename.contains('/') || filename.contains('\\')) {
        throw detail::Error("Invalid generated filename: expected a single non-dot filename: " + filename);
    }
    if (!output_.empty()) throw detail::Error("Custom action '" + name_ + "' permits only one output");
    output_ = (detail::fs::absolute(owner_->environment().project_root) /
               ".cxx" / "generated" / name_ / filename).string();
    return GeneratedFile(owner_, this, output_);
}

CustomAction& CustomAction::command(std::vector<std::string> argv) {
    if (argv.empty()) throw detail::Error("Custom action '" + name_ + "' requires a command");
    require_text(argv.front(), "custom tool");
    for (const auto& argument : argv) {
        if (argument.contains('\0')) throw detail::Error("Custom command arguments must not contain NUL bytes");
    }
    arguments_ = std::move(argv);
    return *this;
}

CustomAction& CustomAction::input(std::string path) {
    require_text(path, "custom input path");
    inputs_.push_back(std::move(path));
    return *this;
}

CustomAction& CustomAction::input(const GeneratedFile& file) {
    if (owner_ != file.owner_) {
        throw detail::Error("Custom action '" + name_ + "' references a generated file from another Build");
    }
    generated_inputs_.push_back(file);
    return *this;
}

CustomAction& CustomAction::cacheable(bool enabled) {
    cacheable_ = enabled;
    return *this;
}

Target& Target::include(std::string path, Visibility visibility) {
    require_text(path, "include path");
    require_visibility(visibility);
    includes_.push_back({std::move(path), visibility});
    return *this;
}

Target& Target::system_include(std::string path, Visibility visibility) {
    require_text(path, "system include path");
    require_visibility(visibility);
    system_includes_.push_back({std::move(path), visibility});
    return *this;
}

Target& Target::optimization(Optimization level) {
    switch (level) {
    case Optimization::default_:
    case Optimization::none:
    case Optimization::speed:
        optimization_ = level;
        return *this;
    }
    throw detail::Error("Invalid optimization level");
}

Target& Target::warnings(bool enabled) {
    warnings_ = enabled;
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

Target& Build::shared_library(std::string name) {
    return add(std::move(name), TargetKind::shared_library);
}

Target& Build::object_library(std::string name) {
    return add(std::move(name), TargetKind::object_library);
}

CustomAction& Build::custom_action(std::string name) {
    if (!safe_target_name(name)) {
        throw detail::Error("Invalid custom action name '" + name + "'; expected [A-Za-z_][A-Za-z0-9_-]*");
    }
    const auto key = target_name_key(name);
    if (std::ranges::any_of(custom_actions_, [&](const auto& action) {
            return target_name_key(action->name()) == key;
        })) {
        throw detail::Error("Duplicate custom action name (ASCII case-insensitive): " + name);
    }
    custom_actions_.push_back(std::unique_ptr<CustomAction>(new CustomAction(this, std::move(name))));
    return *custom_actions_.back();
}

void Build::test(std::string name, Target& executable, std::vector<std::string> args) {
    require_text(name, "test name");
    if (executable.owner_ != this || executable.kind() != TargetKind::executable) {
        throw detail::Error("Test target must be an executable owned by this Build: " + name);
    }
    for (const auto& test : tests_) {
        if (target_name_key(test.name) == target_name_key(name))
            throw detail::Error("Duplicate test name (ASCII case-insensitive): " + name);
    }
    for (const auto& arg : args) {
        if (arg.contains('\0')) throw detail::Error("Test arguments must not contain NUL bytes");
    }
    tests_.push_back({std::move(name), &executable, std::move(args)});
}

void Build::install(Target& target) {
    if (target.owner_ != this) throw detail::Error("Install target belongs to another Build");
    if (target.kind() == TargetKind::object_library)
        throw detail::Error("Object libraries cannot be installed: " + target.name());
    if (std::ranges::contains(installs_, &target))
        throw detail::Error("Duplicate install target: " + target.name());
    installs_.push_back(&target);
}

void Build::install_file(std::string source, std::string destination) {
    require_text(source, "install source");
    require_install_destination(destination);
    install_files_.push_back({std::move(source), std::move(destination), nullptr});
}

void Build::install_file(const GeneratedFile& source, std::string destination) {
    if (source.owner_ != this) throw detail::Error("Install source belongs to another Build");
    require_install_destination(destination);
    install_files_.push_back({source.path(), std::move(destination), &source.producer()});
}

namespace detail {
namespace {

struct Command {
    bool run = false;
    bool test = false;
    bool install = false;
    std::string filter;
    std::string prefix;
    bool verbose = false;
    std::vector<std::string> targets;
    std::vector<std::string> arguments;
};

Command parse_command(int argc, char** argv) {
    if (argc < 2 || !argv || !argv[1]) {
        throw Error("Usage: driver build [target...] [-v] | run [target] [-v] [-- args...] | "
                    "test [filter] [-v] | install --prefix <directory> [-v]");
    }
    Command command;
    const std::string action = argv[1];
    if (action == "run") command.run = true;
    else if (action == "test") command.test = true;
    else if (action == "install") command.install = true;
    else if (action != "build") throw Error("Unknown command '" + action + "'; expected build, run, test or install");
    bool application_arguments = false, has_filter = false;
    for (int i = 2; i < argc; ++i) {
        if (!argv[i]) throw Error("Invalid null command argument");
        const std::string argument = argv[i];
        if (application_arguments) command.arguments.push_back(argument);
        else if (argument == "-v") {
            if (command.verbose) throw Error("Duplicate option: -v");
            command.verbose = true;
        } else if (argument == "--prefix" && command.install) {
            if (!command.prefix.empty()) throw Error("Duplicate option: --prefix");
            if (++i == argc || !argv[i] || !argv[i][0] || argv[i][0] == '-')
                throw Error("install requires --prefix <nonempty directory>");
            command.prefix = argv[i];
        } else if (command.test && !argument.starts_with('-')) {
            if (has_filter) throw Error("test accepts at most one filter");
            command.filter = argument;
            has_filter = true;
        } else if (argument == "--" && command.run) application_arguments = true;
        else if (command.install || command.test || !safe_target_name(argument))
            throw Error("Invalid option or target: " + argument);
        else command.targets.push_back(argument);
    }
    if (command.install && command.prefix.empty()) throw Error("install requires an explicit --prefix <directory>");
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

// Resolve aliases for ownership checks only; command/source spelling stays intact.
std::string output_identity(const fs::path& path) {
    return target_name_key(fs::weakly_canonical(path).string());
}

std::vector<const CustomAction*> validate_custom_graph(const Build& build) {
    const fs::path root = build.environment().project_root;
    std::set<const CustomAction*> members;
    std::set<std::string> outputs;
    for (const auto& action : build.custom_actions()) {
        members.insert(action.get());
        if (action->output_path().empty()) throw Error("Custom action '" + action->name() + "' has no output");
        const fs::path output = action->output_path();
        // A symlinked generated directory could overwrite sources/compiler state.
        for (const auto& directory : {root / ".cxx", root / ".cxx/generated", output.parent_path()}) {
            const auto status = fs::symlink_status(directory);
            if (fs::is_symlink(status) || (fs::exists(status) && !fs::is_directory(status))) {
                throw Error("Generated output directory must be a real directory: " + directory.string());
            }
        }
        for (const auto& path : {output, fs::path(output.string() + ".key")}) {
            const auto status = fs::symlink_status(path);
            if (fs::is_symlink(status) || (fs::exists(status) && !fs::is_regular_file(status))) {
                throw Error("Generated output/metadata must be a regular file: " + path.string());
            }
            if (!outputs.insert(output_identity(path)).second) {
                throw Error("Multiple writers for generated output/metadata: " + path.string());
            }
        }
        const auto& args = action->arguments();
        if (args.empty()) throw Error("Custom action '" + action->name() + "' has no command");
        if (std::find(args.begin() + 1, args.end(), output.string()) == args.end()) {
            throw Error("Custom action '" + action->name() +
                        "' command must explicitly name its output as a standalone argument: " + output.string());
        }
    }
    auto require_handle = [&](const GeneratedFile& file) {
        if (!members.contains(&file.producer()) || file.producer().output_path() != file.path()) {
            throw Error("Generated file references a producer outside this Build");
        }
    };
    auto require_input = [&](const fs::path& path) {
        if (outputs.contains(output_identity(path))) {
            throw Error("Input conflicts with generated output/metadata; use a GeneratedFile handle: " + path.string());
        }
        if (!fs::is_regular_file(path)) throw Error("Missing custom action input: " + path.string());
    };
    for (const auto& file : build.install_files()) {
        if (file.producer) {
            if (!members.contains(file.producer) || file.producer->output_path() != file.source)
                throw Error("Install source references a producer outside this Build");
        } else require_input(project_path(root, file.source));
    }
    for (const auto& action : build.custom_actions()) {
        for (const auto& file : action->generated_inputs()) require_handle(file);
        for (const auto& input : action->inputs()) require_input(project_path(root, input));
    }
    for (const auto& target : build.targets()) {
        for (const auto& file : target->generation_dependencies()) require_handle(file);
        for (const auto& source : target->sources()) {
            const auto path = project_path(root, source);
            const bool generated = std::ranges::any_of(target->generated_sources(), [&](const auto& file) {
                return file.path() == path.string();
            });
            if (!generated && outputs.contains(output_identity(path))) {
                throw Error("Source conflicts with generated output/metadata; use a GeneratedFile handle: " + path.string());
            }
        }
    }
    std::map<const CustomAction*, int> state;
    std::vector<const CustomAction*> stack, order;
    auto visit = [&](this auto&& self, const CustomAction* action) -> void {
        if (state[action] == 2) return;
        if (state[action] == 1) {
            std::string cycle;
            for (auto it = std::ranges::find(stack, action); it != stack.end(); ++it) cycle += (*it)->name() + " -> ";
            throw Error("Custom action dependency cycle: " + cycle + action->name());
        }
        state[action] = 1;
        stack.push_back(action);
        for (const auto& file : action->generated_inputs()) self(&file.producer());
        stack.pop_back();
        state[action] = 2;
        order.push_back(action);
    };
    for (const auto& action : build.custom_actions()) visit(action.get());
    return order;
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
        if (target->kind() == TargetKind::object_library && target->sources().empty()) {
            throw Error("Object library '" + target->name() + "' has no sources; declare at least one source");
        }
        for (const auto& source : target->sources()) {
            const auto path = project_path(root, source);
            const bool generated = std::ranges::any_of(target->generated_sources(), [&](const auto& file) {
                return file.path() == path.string();
            });
            if (!generated && !fs::is_regular_file(path)) {
                throw Error("Missing source for target '" + target->name() + "': " + path.string());
            }
        }
    }
    for (const auto& target : build.targets()) {
        for (const auto& edge : target->dependencies()) {
            if (!members.contains(edge.first)) {
                throw Error("Target '" + target->name() + "' references a target outside this Build");
            }
            if (edge.first->kind() == TargetKind::executable) {
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
    if (command.install) return build.installs();
    if (command.test) {
        if (build.tests().empty()) throw Error("No tests registered");
        for (const auto& test : build.tests()) {
            if (test.name.contains(command.filter)) selected.push_back(test.executable);
        }
        if (selected.empty()) throw Error("No tests match filter: " + command.filter);
        return selected;
    }
    for (const auto& name : command.targets) {
        const auto found = std::ranges::find(build.targets(), name, &Target::name);
        if (found == build.targets().end()) {
            if (std::ranges::any_of(build.custom_actions(), [&](const auto& action) {
                    return target_name_key(action->name()) == target_name_key(name);
                })) {
                throw Error("Standalone custom action selection is not supported: " + name +
                            "; use build with no targets or select a consuming target");
            }
            throw Error("Unknown target: " + name);
        }
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
    std::vector<std::string> system_includes;
    std::vector<std::string> defines;
};

template <class T>
void append_unique(std::vector<T>& values, const T& value) {
    if (!std::ranges::contains(values, value)) values.push_back(value);
}

void merge_usage(CompileUsage& into, const CompileUsage& from) {
    for (const auto& value : from.includes) append_unique(into.includes, value);
    for (const auto& value : from.system_includes) append_unique(into.system_includes, value);
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
    for (const auto& usage : target.system_includes()) {
        if (visible(usage.visibility)) append_unique(result.system_includes, usage.value);
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
    switch (target.kind()) {
    case TargetKind::executable: return directory / target.name();
    case TargetKind::static_library: return directory / ("lib" + target.name() + ".a");
    case TargetKind::object_library: return directory / ".objects";
    case TargetKind::shared_library:
#ifdef __APPLE__
        return directory / ("lib" + target.name() + ".dylib");
#else
        return directory / ("lib" + target.name() + ".so");
#endif
    }
    throw Error("Invalid target kind for '" + target.name() + "'");
}

struct LinkInputs {
    // Concrete action inputs. Object entries name only their own compiled files,
    // not recursively expanded targets.
    std::vector<const Target*> objects;
    std::vector<const Target*> libraries;
};

// A raw object's unresolved libraries travel with that object's files. Keeping
// this provenance lets a resolving link discharge just that group, while an
// independent public requirement for the same library survives.
struct ObjectRequirement {
    const Target* object;
    std::vector<const Target*> libraries;
};

struct LinkRequirements {
    std::vector<ObjectRequirement> objects;
    // Independent requirements, including shared providers substituted for raw
    // objects. These survive even when every object group is discharged.
    std::vector<const Target*> libraries;
};

void merge_libraries(std::vector<const Target*>& into, const std::vector<const Target*>& from) {
    for (const auto* library : from) append_unique(into, library);
}

void merge_objects(LinkRequirements& into, const LinkRequirements& from) {
    for (const auto& object : from.objects) {
        const auto found = std::ranges::find(into.objects, object.object, &ObjectRequirement::object);
        if (found == into.objects.end()) into.objects.push_back(object);
        else merge_libraries(found->libraries, object.libraries);
    }
}

void merge_requirements(LinkRequirements& into, const LinkRequirements& from) {
    merge_objects(into, from);
    merge_libraries(into.libraries, from.libraries);
}

LinkInputs flatten_requirements(const LinkRequirements& requirements) {
    LinkInputs result;
    result.libraries = requirements.libraries;
    for (const auto& object : requirements.objects) {
        result.objects.push_back(object.object);
        merge_libraries(result.libraries, object.libraries);
    }
    return result;
}

void remove_stored_objects(LinkRequirements& requirements, const std::vector<const Target*>& stored) {
    std::erase_if(requirements.objects, [&](const ObjectRequirement& object) {
        return std::ranges::contains(stored, object.object);
    });
}

void resolve_object_providers(LinkRequirements& requirements,
                              const std::map<const Target*, LinkInputs>& local,
                              const std::map<const Target*, LinkRequirements>& exported) {
    // Freeze the candidates before removing any group. A library required by one
    // raw object may also provide a different, independently published object.
    // Promoting that provider preserves the latter requirement through later links.
    const auto candidates = flatten_requirements(requirements).libraries;
    std::erase_if(requirements.objects, [&](const ObjectRequirement& object) {
        for (const auto* library : candidates) {
            // Archives are lazy; only a shared artifact proves object presence.
            // Its private runtime closure is not part of its physical storage.
            if (library->kind() != TargetKind::shared_library ||
                !std::ranges::contains(local.at(library).objects, object.object)) continue;
            merge_libraries(requirements.libraries, exported.at(library).libraries);
            return true;
        }
        return false;
    });
}

std::vector<const Target*> order_libraries(const std::vector<const Target*>& libraries,
                                         const std::map<const Target*, LinkRequirements>& exported) {
    // Stable Kahn ordering: each library precedes its still-visible requirements,
    // including link-only dependencies of objects absorbed by a static archive.
    std::map<const Target*, std::size_t> consumers;
    for (const auto* library : libraries) consumers.emplace(library, 0);
    for (const auto* library : libraries) {
        for (const auto* dependency : flatten_requirements(exported.at(library)).libraries) {
            if (dependency != library && consumers.contains(dependency)) ++consumers.at(dependency);
        }
    }
    std::vector<const Target*> ready;
    for (const auto* library : libraries) {
        if (consumers.at(library) == 0) ready.push_back(library);
    }
    for (std::size_t i = 0; i < ready.size(); ++i) {
        for (const auto* dependency : flatten_requirements(exported.at(ready[i])).libraries) {
            if (dependency != ready[i] && consumers.contains(dependency) &&
                --consumers.at(dependency) == 0) ready.push_back(dependency);
        }
    }
    if (ready.size() != libraries.size()) throw Error("Dependency cycle in library link order");
    return ready;
}

std::map<const Target*, LinkInputs> link_inputs(const std::vector<const Target*>& order) {
    std::map<const Target*, LinkInputs> local;
    std::map<const Target*, LinkRequirements> exported;
    for (const auto* target : order) {
        LinkRequirements requirements;
        for (const auto& [dependency, visibility] : target->dependencies()) {
            if (visibility != Visibility::interface_) merge_requirements(requirements, exported.at(dependency));
        }
        if (target->sources().empty() && requirements.objects.empty()) {
            throw Error("Target '" + target->name() + "' has no sources or local object inputs");
        }
        // Only resolving actions replace explicit objects with shared providers.
        // Static archives keep their physical members and unresolved libraries.
        if (target->kind() == TargetKind::executable || target->kind() == TargetKind::shared_library)
            resolve_object_providers(requirements, local, exported);
        auto& own = local[target];
        own = flatten_requirements(requirements);
        own.libraries = order_libraries(own.libraries, exported);

        LinkRequirements interface;
        if (target->kind() == TargetKind::object_library) {
            ObjectRequirement object{target, {}};
            for (const auto& [dependency, visibility] : target->dependencies()) {
                const auto& dependency_interface = exported.at(dependency);
                // Nested object groups retain their own unresolved libraries.
                merge_objects(interface, dependency_interface);
                // Keep local needs even for public edges: a later private edge
                // to this collection can make its public libraries link-only.
                if (visibility != Visibility::interface_)
                    merge_libraries(object.libraries, dependency_interface.libraries);
                if (visibility != Visibility::private_)
                    merge_libraries(interface.libraries, dependency_interface.libraries);
            }
            interface.objects.insert(interface.objects.begin(), std::move(object));
        } else if (target->kind() == TargetKind::static_library) {
            interface.libraries.push_back(target);
            // Archiving stores objects but resolves none of their references.
            merge_libraries(interface.libraries, own.libraries);
            for (const auto& [dependency, visibility] : target->dependencies()) {
                if (visibility == Visibility::interface_)
                    merge_requirements(interface, exported.at(dependency));
            }
            remove_stored_objects(interface, own.objects);
        } else if (target->kind() == TargetKind::shared_library) {
            for (const auto& [dependency, visibility] : target->dependencies()) {
                if (visibility != Visibility::private_)
                    merge_requirements(interface, exported.at(dependency));
            }
            // Discharge private requirements only for objects actually stored
            // here. Remaining published objects need raw files or a public
            // provider, even if a private local DSO supplied them at this link.
            remove_stored_objects(interface, own.objects);
            resolve_object_providers(interface, local, exported);
            interface.libraries.insert(interface.libraries.begin(), target);
        }
        exported.emplace(target, std::move(interface));
    }
    return local;
}

std::vector<const Target*> runtime_libraries(const LinkInputs& inputs,
                                            const std::map<const Target*, LinkInputs>& local) {
    std::vector<const Target*> result;
    auto collect = [&](this auto&& self, const LinkInputs& links) -> void {
        for (const auto* library : links.libraries) {
            if (library->kind() != TargetKind::shared_library || std::ranges::contains(result, library)) continue;
            result.push_back(library);
            self(local.at(library));
        }
    };
    collect(inputs);
    return result;
}

fs::path install_artifact(const fs::path& root, const Target& target) {
    if (target.kind() == TargetKind::static_library) return artifact(root, target);
    return root / ".cxx/install" / (target.kind() == TargetKind::executable ? "bin" : "lib") /
           artifact(root, target).filename();
}

struct InstallEntry {
    fs::path source;
    fs::path destination;
};

// Component-aware and case-folded: a file also conflicts with a child path.
bool contains_path(const std::string& parent, const std::string& path) {
    return path == parent || path.starts_with(parent + "/");
}

std::vector<InstallEntry> install_entries(const Build& build,
                                         const std::map<const Target*, LinkInputs>& links,
                                         std::vector<const Target*>& installed) {
    installed = build.installs();
    for (const auto* target : build.installs()) {
        if (target->kind() == TargetKind::static_library) continue;
        for (const auto* library : runtime_libraries(links.at(target), links)) append_unique(installed, library);
    }
    const fs::path root = build.environment().project_root;
    std::vector<InstallEntry> entries;
    for (const auto* target : installed) {
        entries.push_back({install_artifact(root, *target),
                          fs::path(target->kind() == TargetKind::executable ? "bin" : "lib") /
                          artifact(root, *target).filename()});
    }
    for (const auto& file : build.install_files()) {
        entries.push_back({project_path(root, file.source), file.destination});
    }
    std::vector<std::string> destinations;
    for (const auto& entry : entries) {
        require_install_destination(entry.destination.generic_string());
        const auto key = target_name_key(entry.destination.generic_string());
        for (const auto& other : destinations) {
            if (contains_path(key, other) || contains_path(other, key))
                throw Error("Install destination collision: " + entry.destination.string());
        }
        destinations.push_back(key);
    }
    return entries;
}

void validate_install_plan(const fs::path& root, const fs::path& prefix,
                           const std::vector<InstallEntry>& entries,
                           const std::vector<fs::path>& protected_inputs) {
    validate_install_path(prefix, true);
    const auto state = output_identity(root / ".cxx");
    if (contains_path(state, output_identity(prefix)))
        throw Error("Install prefix overlaps project build state (.cxx): " + prefix.string());
    for (const auto& entry : entries) {
        const auto destination = prefix / entry.destination;
        validate_install_path(destination);
        const auto key = output_identity(destination);
        if (contains_path(state, key) || contains_path(key, state))
            throw Error("Install destination overlaps project build state: " + destination.string());
        auto check_source = [&](const fs::path& source) {
            const auto input = output_identity(source);
            if (contains_path(input, key) || contains_path(key, input) ||
                (fs::exists(destination) && fs::exists(source) && fs::equivalent(source, destination)))
                throw Error("Install destination overlaps source/build artifact: " + destination.string());
        };
        for (const auto& source : protected_inputs) check_source(source);
        for (const auto& other : entries) check_source(other.source);
    }
}

enum class ActionKind { custom, compile, archive, link, test, install };
struct Action {
    ActionKind kind;
    std::vector<std::string> arguments;
    std::vector<fs::path> inputs;
    fs::path output;
    std::vector<std::size_t> predecessors;
    bool cacheable = true;
    std::string label = {};
};

// The plan is stored separately from execution. Producers precede consumers in
// this sequential M0 schedule; M1 can schedule the same explicit dependency edges.
void append_action(std::vector<Action>& graph, std::map<std::string, std::size_t>& producers,
                   Action action) {
    for (const auto& input : action.inputs) {
        const auto producer = producers.find(output_identity(input));
        if (producer != producers.end()) append_unique(action.predecessors, producer->second);
        else if (!fs::is_regular_file(input)) throw Error("Missing action input: " + input.string());
    }
    // Metadata is also a write: an output named like another action's .key
    // must be rejected before any producer runs.
    std::vector<fs::path> outputs;
    if (!action.output.empty()) {
        outputs.push_back(action.output);
        if (action.kind != ActionKind::install) outputs.emplace_back(action.output.string() + ".key");
    }
    for (const auto& path : outputs) {
        if (!producers.emplace(output_identity(path), graph.size()).second) {
            throw Error("Multiple writers for output/metadata: " + path.string());
        }
    }
    graph.push_back(std::move(action));
}

int build_targets(const Build& build, const std::vector<const Target*>& order,
                  const std::vector<const CustomAction*>& custom_order,
                  const std::vector<const Target*>& selected, const Command& command,
                  DependencyScan protected_inputs) {
    if (command.install && !protected_inputs.complete)
        throw Error("Install input protection is incomplete: bootstrap dependency scan is lossy or untracked");
    const bool verbose = command.verbose;
    const bool all_custom = !command.run && !command.test && !command.install && command.targets.empty();
    std::set<const Target*> closure;
    for (const auto* target : selected) add_closure(target, closure);
    const auto usages = compile_usages(order);
    const auto links = link_inputs(order);
    const auto& environment = build.environment();
    const fs::path root = environment.project_root;
    std::vector<const Target*> installed;
    const auto entries = install_entries(build, links, installed);
    const auto prefix = command.install ? project_path(root, command.prefix) : fs::path{};
    std::vector<Action> graph;
    std::map<std::string, std::size_t> producers;
    std::set<std::size_t> requested;
    for (const auto* custom : custom_order) {
        auto args = custom->arguments();
        const fs::path tool = args.front();
        args.front() = resolve_program(tool.has_parent_path() ? project_path(root, tool.string()).string()
                                                              : tool.string()).string();
        std::vector<fs::path> inputs{args.front()};
        for (const auto& input : custom->inputs()) append_unique(inputs, project_path(root, input));
        for (const auto& file : custom->generated_inputs()) {
            append_unique(inputs, fs::path(file.path()));
            // Propagate the declared producer closure even if its output bytes
            // stay the same. Both files are produced by the same predecessor.
            append_unique(inputs, fs::path(file.path() + ".key"));
        }
        if (all_custom) requested.insert(graph.size());
        append_action(graph, producers, {ActionKind::custom, std::move(args), std::move(inputs),
                                        custom->output_path(), {}, custom->is_cacheable()});
    }
    std::map<const Target*, std::vector<fs::path>> objects;
    auto append_link = [&](const Target* target, const fs::path& output,
                           std::vector<fs::path> inputs, const std::vector<std::size_t>& prerequisites,
                           bool install_layout) {
        const auto& standard = target->standard().empty() ? environment.cpp_standard : target->standard();
        std::vector<std::string> args{environment.compiler, "-std=c++" + standard};
        if (target->kind() == TargetKind::shared_library) {
#ifdef __APPLE__
            args.insert(args.end(), {"-dynamiclib", "-Xlinker", "-install_name", "-Xlinker",
                                     "@rpath/" + output.filename().string()});
#else
            args.insert(args.end(), {"-shared", "-Xlinker", "-soname", "-Xlinker", output.filename().string()});
#endif
        }
        if (install_layout) {
#ifdef __APPLE__
            const std::string origin = "@loader_path";
#else
            const std::string origin = "$ORIGIN";
#endif
            args.insert(args.end(), {"-Xlinker", "-rpath", "-Xlinker",
                                     origin + (target->kind() == TargetKind::executable ? "/../lib" : "")});
        }
        for (const auto& input : inputs) args.push_back(input.string());
        auto library_path = [&](const Target* library) {
            return install_layout ? install_artifact(root, *library) : artifact(root, *library);
        };
        for (const auto* library : links.at(target).libraries) {
            const auto path = library_path(library);
            args.push_back(path.string());
            append_unique(inputs, path);
            if (!install_layout && library->kind() == TargetKind::shared_library) {
                const auto relative = path.parent_path().lexically_relative(output.parent_path()).generic_string();
#ifdef __APPLE__
                const auto lookup = "@loader_path/" + relative;
#else
                const auto lookup = "$ORIGIN/" + relative;
#endif
                // Separate argv values preserve paths containing commas.
                args.insert(args.end(), {"-Xlinker", "-rpath", "-Xlinker", lookup});
            }
        }
        for (const auto* library : runtime_libraries(links.at(target), links)) {
            const auto path = library_path(library);
            // Private DSOs are inputs, not explicit public link arguments.
            append_unique(inputs, path);
#ifndef __APPLE__
            args.insert(args.end(), {"-Xlinker", "-rpath-link", "-Xlinker", path.parent_path().string()});
#endif
        }
        args.insert(args.end(), {"-o", output.string()});
        append_action(graph, producers, {ActionKind::link, std::move(args), std::move(inputs), output, prerequisites});
    };
    for (const auto* target : order) {
        const auto first_action = graph.size();
        std::vector<std::size_t> prerequisites;
        for (const auto& file : target->generation_dependencies()) {
            append_unique(prerequisites, producers.at(output_identity(file.path())));
        }
        const auto output = artifact(root, *target);
        const auto& standard = target->standard().empty() ? environment.cpp_standard : target->standard();
        std::vector<std::string> flags{"-std=c++" + standard};
        // Compile every library as PIC so static/object inputs can be reused in
        // shared libraries without variants. This trades some static-only code
        // generation performance for a simple, consistent object identity.
        if (target->kind() != TargetKind::executable) flags.push_back("-fPIC");
        switch (target->optimization()) {
        case Optimization::default_: break;
        case Optimization::none: flags.push_back("-O0"); break;
        case Optimization::speed: flags.push_back("-O2"); break;
        }
        if (target->warnings_enabled()) {
            flags.insert(flags.end(), {"-Wall", "-Wextra", "-Wpedantic"});
        }
        for (const auto& include : usages.at(target).includes) {
            flags.push_back("-I" + project_path(root, include).string());
        }
        for (const auto& include : usages.at(target).system_includes) {
            flags.push_back("-isystem");
            flags.push_back(project_path(root, include).string());
        }
        for (const auto& define : usages.at(target).defines) flags.push_back("-D" + define);
        auto& own_objects = objects[target];
        std::set<fs::path> sources;
        for (const auto& source : target->sources()) {
            const auto path = project_path(root, source);
            if (!sources.insert(path).second) continue;
            // Include the owner in the basename: archives may absorb the same
            // source compiled by different object targets with different flags.
            const auto object = output.parent_path() / ".objects" /
                (fingerprint({target->name(), path.string()}) + ".o");
            append_action(graph, producers, {ActionKind::compile, flags, {path}, object, prerequisites});
            own_objects.push_back(object);
        }
        if (!command.install && closure.contains(target)) {
            for (auto i = first_action; i < graph.size(); ++i) requested.insert(i);
        }
        if (target->kind() == TargetKind::object_library) continue;
        auto inputs = own_objects;
        for (const auto* object_target : links.at(target).objects) {
            for (const auto& object : objects.at(object_target)) append_unique(inputs, object);
        }
        if (target->kind() == TargetKind::static_library) {
            std::vector<std::string> args{environment.archiver, "rcs", output.string()};
            for (const auto& input : inputs) args.push_back(input.string());
            append_action(graph, producers, {ActionKind::archive, std::move(args), std::move(inputs), output, prerequisites});
        } else {
            append_link(target, output, std::move(inputs), prerequisites, false);
        }
        if (!command.install && closure.contains(target)) requested.insert(graph.size() - 1);
    }
    if (command.install) {
        for (const auto* target : order) {
            if (!std::ranges::contains(installed, target) || target->kind() == TargetKind::static_library) continue;
            auto inputs = objects.at(target);
            for (const auto* object : links.at(target).objects) {
                for (const auto& path : objects.at(object)) append_unique(inputs, path);
            }
            const auto output = install_artifact(root, *target);
            validate_install_path(output);
            validate_install_path(output.string() + ".key");
            std::vector<std::size_t> prerequisites;
            for (const auto& file : target->generation_dependencies())
                append_unique(prerequisites, producers.at(output_identity(file.path())));
            append_link(target, output, std::move(inputs), prerequisites, true);
        }
    }
    // Protect declared inputs and all build outputs, including unselected ones.
    // Check the whole destination set before any project action can run.
    if (command.install) {
        for (const auto& input : std::vector<fs::path>{root / "build.cpp", root / "cxxrc.toml",
                                                     environment.compiler, environment.archiver})
            append_unique(protected_inputs.paths, input);
        for (const auto& action : graph) {
            for (const auto& input : action.inputs) append_unique(protected_inputs.paths, input);
            append_unique(protected_inputs.paths, action.output);
            append_unique(protected_inputs.paths, fs::path(action.output.string() + ".key"));
        }
        validate_install_plan(root, prefix, entries, protected_inputs.paths);
    }

    // Terminal actions share the producer graph. They have no cache keys; a
    // cache hit for their prerequisites never suppresses the terminal effect.
    std::vector<std::size_t> terminal_prerequisites(requested.begin(), requested.end());
    if (command.install) {
        for (const auto& entry : entries) {
            const auto producer = producers.find(output_identity(entry.source));
            if (producer != producers.end()) append_unique(terminal_prerequisites, producer->second);
        }
        for (const auto& entry : entries) {
            requested.insert(graph.size());
            append_action(graph, producers, {ActionKind::install, {}, {entry.source},
                                            prefix / entry.destination, terminal_prerequisites, false});
            terminal_prerequisites = {graph.size() - 1};
        }
    } else if (command.test) {
        for (const auto& test : build.tests()) {
            if (!test.name.contains(command.filter)) continue;
            const auto executable = artifact(root, *test.executable);
            std::vector<std::string> args{executable.string()};
            args.insert(args.end(), test.arguments.begin(), test.arguments.end());
            requested.insert(graph.size());
            append_action(graph, producers, {ActionKind::test, std::move(args), {executable}, {},
                                            terminal_prerequisites, false, test.name});
            terminal_prerequisites = {graph.size() - 1};
        }
    }
    // Validate the complete plan, including unselected declarations. Ordinary
    // inputs must not silently read an old output of a later/self producer.
    for (std::size_t index = 0; index < graph.size(); ++index) {
        const auto& action = graph[index];
        if (action.kind == ActionKind::custom && producers.contains(output_identity(action.arguments.front()))) {
            throw Error("Custom action requires an existing system host tool, not a built tool: " + action.arguments.front());
        }
        for (const auto& input : action.inputs) {
            const auto producer = producers.find(output_identity(input));
            if (producer != producers.end() && producer->second >= index) {
                throw Error("Action input conflicts with an output; declare generation dependencies with handles: " + input.string());
            }
        }
        for (const auto predecessor : action.predecessors) {
            if (predecessor >= index) throw Error("Invalid action dependency order");
        }
    }
    std::set<std::size_t> scheduled;
    auto schedule = [&](this auto&& self, std::size_t index) -> void {
        if (!scheduled.insert(index).second) return;
        for (const auto predecessor : graph[index].predecessors) self(predecessor);
    };
    for (const auto index : requested) schedule(index);
    // Derive tool identities from the selected action closure, including files
    // that consume a build artifact without an explicit install(Target) entry.
    const bool uses_compiler = std::ranges::any_of(scheduled, [&](std::size_t index) {
        return graph[index].kind == ActionKind::compile || graph[index].kind == ActionKind::link;
    });
    const bool uses_archiver = std::ranges::any_of(scheduled, [&](std::size_t index) {
        return graph[index].kind == ActionKind::archive;
    });
    const auto compiler_identity = uses_compiler ? hash_file(environment.compiler) : std::string{};
    const auto archiver_identity = uses_archiver ? hash_file(environment.archiver) : std::string{};
    std::size_t compiled = 0, archived = 0, linked = 0, generated = 0;
    bool checked_install_sources = false;
    for (std::size_t index = 0; index < graph.size(); ++index) {
        if (!scheduled.contains(index)) continue;
        const auto& action = graph[index];
        if (action.kind == ActionKind::custom) {
            if (file_action("custom", action.arguments, action.inputs, action.output,
                            hash_file(action.arguments.front()), root, verbose, action.cacheable)) ++generated;
        } else if (action.kind == ActionKind::compile) {
            if (compile_cpp(environment.compiler, action.inputs.front(), action.output,
                            action.arguments, compiler_identity, root, verbose, "compile",
                            command.install ? &protected_inputs : nullptr)) ++compiled;
        } else if (action.kind == ActionKind::archive) {
            if (file_action("archive", action.arguments, action.inputs, action.output,
                            archiver_identity, root, verbose)) ++archived;
        } else if (action.kind == ActionKind::link) {
            if (file_action("link", action.arguments, action.inputs, action.output,
                            compiler_identity, root, verbose)) ++linked;
        } else if (action.kind == ActionKind::test) {
            std::cerr << "[test] " << action.label << '\n';
            const auto code = run_process(action.arguments, root, verbose);
            if (code != 0) return code;
        } else if (action.kind == ActionKind::install) {
            if (!checked_install_sources) {
                // Producers may have changed paths since preflight. Recheck the
                // entire set before the first prefix write, not file by file.
                if (!protected_inputs.complete)
                    throw Error("Install input protection is incomplete: compile dependency scan is lossy or untracked");
                validate_install_plan(root, prefix, entries, protected_inputs.paths);
                for (const auto& entry : entries) {
                    if (!fs::is_regular_file(entry.source))
                        throw Error("Missing install source: " + entry.source.string());
                }
                checked_install_sources = true;
            }
            install_file_atomic(action.inputs.front(), action.output);
        }
    }
    std::cerr << "[build] targets=" << closure.size() << " compiled=" << compiled
              << " archived=" << archived << " linked=" << linked << " generated=" << generated << '\n';
    return 0;
}

} // namespace

int driver_main(int argc, char** argv, void (*declaration)(cxx::Build&), Environment environment,
                const std::string& bootstrap_inputs) {
    try {
        const auto command = parse_command(argc, argv);
        if (!declaration) throw Error("Missing build declaration function");
        Build build(resolved_environment(std::move(environment)));
        declaration(build);
        const auto custom_order = validate_custom_graph(build);
        const auto order = validate_graph(build);
        const auto selected = select_targets(build, command);
        const auto code = build_targets(build, order, custom_order, selected, command,
                                       command.install ? read_dependency_scan(bootstrap_inputs) : DependencyScan{});
        if (code != 0) return code;
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
