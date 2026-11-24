// Each test is its own CTest entry: `stats_tests <name>` runs one and returns 0 on success.

#include <cmath>
#include <iomanip>
#include <iostream>
#include <stdexcept>
#include <string>

#include "stats.hpp"

#define CHECK_NEAR(actual, expected)                                                    \
    do {                                                                                \
        const double got_ = (actual);                                                   \
        const double want_ = (expected);                                                \
        if (std::fabs(got_ - want_) > 1e-9) {                                           \
            std::cerr << __FILE__ << ":" << __LINE__ << ": " << #actual                 \
                      << std::setprecision(10) << ": expected " << want_ << ", got " \
                      << got_ << "\n";                                                  \
            return 1;                                                                   \
        }                                                                               \
    } while (0)

static int average_of_readings() {
    CHECK_NEAR(stats::average({20.5, 21.5, 22.0}), 64.0 / 3.0);
    return 0;
}

static int average_of_whole_numbers() {
    CHECK_NEAR(stats::average({10.0, 20.0, 30.0}), 20.0);
    return 0;
}

static int total_of_readings() {
    CHECK_NEAR(stats::total({0.5, 0.25, 0.25}), 1.0);
    return 0;
}

static int highest_reading() {
    CHECK_NEAR(stats::highest({3.5, 9.25, -1.0}), 9.25);
    return 0;
}

static int empty_readings_are_rejected() {
    try {
        stats::average({});
    } catch (const std::invalid_argument&) {
        return 0;
    }
    std::cerr << __FILE__ << ":" << __LINE__ << ": average({}) did not throw\n";
    return 1;
}

int main(int argc, char** argv) {
    struct Test {
        const char* name;
        int (*run)();
    };
    const Test tests[] = {
        {"average_of_readings", average_of_readings},
        {"average_of_whole_numbers", average_of_whole_numbers},
        {"total_of_readings", total_of_readings},
        {"highest_reading", highest_reading},
        {"empty_readings_are_rejected", empty_readings_are_rejected},
    };
    if (argc != 2) {
        std::cerr << "usage: stats_tests <test name>\n";
        return 2;
    }
    for (const Test& test : tests) {
        if (std::string(argv[1]) == test.name) {
            return test.run();
        }
    }
    std::cerr << "unknown test: " << argv[1] << "\n";
    return 2;
}
