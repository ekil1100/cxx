#include "api.hpp"

#include <stdexcept>

static_assert(__cplusplus == 201703L, "Compile the declaration boundary as C++17");

namespace p02 {
namespace { int calls = 0; }
int declaration_calls() { return calls; }

void build(Build& b) {
    if (++calls != 1) throw std::runtime_error("Declaration was rerun");
    const int major = b.variant() == "incompatible" ? 2 : 1;
    const auto widget = b.dependency("widget", major);
    const auto saved = widget;
    // Force storage growth after taking a handle. Neither handle exposes files or versions.
    for (int i = 0; i < 256; ++i) b.dependency("widget", major);
    b.link("app", widget, b.variant() == "bad-export" ? "missing" : "widget::widget");
    b.link("second-app", saved, "widget::widget");
    if (b.variant() == "added") {
        b.link("app", b.dependency("extra", 1), "extra::extra");
    }
    if (b.variant() == "script-fail") throw std::runtime_error("Fixture declaration failed");
}
} // namespace p02
