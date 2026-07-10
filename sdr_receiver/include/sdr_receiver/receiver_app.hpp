#pragma once
#include "sdr_receiver/calibration.hpp"
#include "sdr_receiver/demodulator.hpp"
#include "sdr_receiver/keyboard.hpp"
#include "sdr_receiver/sdr_controller.hpp"
#include "sdr_receiver/types.hpp"
#include "sdr_receiver/terminal_ui.hpp"
#include <memory>

namespace sdr_receiver {

class ReceiverApp {
public:
    explicit ReceiverApp(std::unique_ptr<ISdrDevice> sdr);
    int run();

    TuneConfig& tune() { return tune_; }
    ReceiverState& state() { return state_; }

private:
    TuneConfig tune_;
    ReceiverState state_;
    DataModel data_;
    std::unique_ptr<ISdrDevice> sdr_;
    SdrController sdr_controller_;
    CalibrationManager calibration_;
    KeyboardController keyboard_;
    Demodulator demodulator_;
    TerminalUi terminal_ui_;
};

} // namespace sdr_receiver
