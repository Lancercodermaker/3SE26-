#pragma once

#include "sdr_receiver/types.hpp"
#include <cstddef>
#include <string>
#include <vector>

namespace sdr_receiver {

class TerminalUi {
public:
    ~TerminalUi();

    void render(const TuneConfig& tune,
                const ReceiverState& state,
                const DataModel& data,
                const RadioParams& radio,
                bool force = false);

private:
    double last_render_sec_ = 0.0;
    bool initialized_ = false;
    size_t last_line_count_ = 0;

    void init_terminal();
    void restore_terminal();
    std::string format_freq(int64_t hz) const;
    std::string lock_text(const ReceiverState& state) const;
    std::string rescue_text(const ReceiverState& state) const;
    std::string cal_text(const ReceiverState& state) const;
    std::string hp_line(const DataModel& data) const;
    std::string position_line(const DataModel& data) const;
    std::string occupation_line(const DataModel& data) const;
    std::vector<std::string> buff_lines(const DataModel& data) const;
    std::string jam_line(const ReceiverState& state) const;
    std::string fit_line(const std::string& text) const;
};

} // namespace sdr_receiver
