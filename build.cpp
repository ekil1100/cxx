#include <cxx/cxx.hpp>
#include "src/support.hpp"

void build(cxx::Build& b) {
    auto& support = b.static_library("cxx_support");
    support.optimization(cxx::Optimization::speed)
           .warnings()
           .source("src/support.cpp")
           .include("include", cxx::Visibility::public_)
           .include("src", cxx::Visibility::public_)
           .system_include("third_party/picosha2", cxx::Visibility::public_);

    b.executable("cxx")
        .optimization(cxx::Optimization::speed)
        .warnings()
        .source("src/main.cpp")
        .system_include("third_party/tomlplusplus")
        .define("CXX_SDK_ROOT=" + cxx::detail::quote_cpp(b.environment().project_root))
        .link(support);

    auto& gtest = b.static_library("gtest");
    gtest.cpp_standard("23")
         .optimization(cxx::Optimization::speed)
         .warnings()
         .source("third_party/googletest/googletest/src/gtest-all.cc")
         .include("third_party/googletest/googletest")
         .system_include("third_party/googletest/googletest/include", cxx::Visibility::public_);

    auto& support_test = b.executable("support_test");
    support_test
        .optimization(cxx::Optimization::speed)
        .warnings()
        .source("tests/support_test.cpp")
        .source("src/runtime.cpp")
        .link(support)
        .link(gtest);
    b.test("support", support_test);
}
