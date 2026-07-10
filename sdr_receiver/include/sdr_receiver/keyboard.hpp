#pragma once
#include "sdr_receiver/calibration.hpp"
#include "sdr_receiver/sdr_controller.hpp"

namespace sdr_receiver {

class KeyboardController {
public:
    KeyboardController(TuneConfig& tune, SdrController& sdr_controller, CalibrationManager& calibration);
    ~KeyboardController();

    void init_input_terminal();
    void restore_input_terminal();
    bool key_pressed() const;
    char read_key() const;
    bool handle_keyboard();

private:
    TuneConfig& tune_;
    SdrController& sdr_controller_;
    CalibrationManager& calibration_;
    bool terminal_initialized_ = false;
    void* old_terminal_settings_ = nullptr;
};

} // namespace sdr_receiver
