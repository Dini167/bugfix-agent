#pragma once

#include <vector>

namespace stats {

// Sum of all readings.
double total(const std::vector<double>& readings);

// Arithmetic mean. Throws std::invalid_argument when there are no readings.
double average(const std::vector<double>& readings);

// Largest reading. Throws std::invalid_argument when there are no readings.
double highest(const std::vector<double>& readings);

}  // namespace stats
