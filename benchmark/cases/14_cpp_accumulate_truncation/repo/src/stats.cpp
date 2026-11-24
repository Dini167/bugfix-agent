#include "stats.hpp"

#include <algorithm>
#include <numeric>
#include <stdexcept>

namespace stats {

double total(const std::vector<double>& readings) {
    return std::accumulate(readings.begin(), readings.end(), 0);
}

double average(const std::vector<double>& readings) {
    if (readings.empty()) {
        throw std::invalid_argument("no readings");
    }
    return total(readings) / static_cast<double>(readings.size());
}

double highest(const std::vector<double>& readings) {
    if (readings.empty()) {
        throw std::invalid_argument("no readings");
    }
    return *std::max_element(readings.begin(), readings.end());
}

}  // namespace stats
