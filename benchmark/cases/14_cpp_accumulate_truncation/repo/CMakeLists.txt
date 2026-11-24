cmake_minimum_required(VERSION 3.16)
project(sensor_stats CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)

add_library(stats src/stats.cpp)
target_include_directories(stats PUBLIC src)

add_executable(stats_tests tests/test_stats.cpp)
target_link_libraries(stats_tests PRIVATE stats)

enable_testing()
foreach(name
    average_of_readings
    average_of_whole_numbers
    total_of_readings
    highest_reading
    empty_readings_are_rejected)
  add_test(NAME ${name} COMMAND stats_tests ${name})
endforeach()
