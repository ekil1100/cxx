#pragma once
#include <cxx/cxx.hpp>

namespace cxx::detail {
// Called by the generated single-TU driver after bootstrap; catches all exceptions.
// argv: driver <build|run|test|install> [command arguments...]
// Environment paths are absolute. Compilation uses the declared C++ standard.
// bootstrap_inputs names the launcher's fresh dependency scan file, written
// under the project lock even on cache hits. This internal entry stays C++17.
int driver_main(int argc, char** argv, void (*declaration)(cxx::Build&),
                cxx::Environment environment, const std::string& bootstrap_inputs);
}
