#pragma once
#include <cxx/cxx.hpp>

namespace cxx::detail {
// Called by the generated single-TU driver after bootstrap; catches all exceptions.
// argv: driver <build|run> [target...] [-v] [-- program-args...]
// Environment paths are absolute. Compilation uses the declared C++ standard.
int driver_main(int argc, char** argv, void (*declaration)(cxx::Build&),
                cxx::Environment environment);
}
