#include "sdr_receiver/keyboard.hpp"
#include "sdr_receiver/config.hpp"
#include <algorithm>
#include <cctype>
#include <iostream>
#ifndef _WIN32
#include <sys/select.h>
#include <termios.h>
#include <unistd.h>
#endif

namespace sdr_receiver {

KeyboardController::KeyboardController(TuneConfig& tune, SdrController& sdr_controller, CalibrationManager& calibration)
    : tune_(tune), sdr_controller_(sdr_controller), calibration_(calibration) {}

KeyboardController::~KeyboardController() { restore_input_terminal(); }

void KeyboardController::init_input_terminal() {
#ifndef _WIN32
    if (!isatty(STDIN_FILENO) || terminal_initialized_) return;
    auto* old_settings = new termios;
    tcgetattr(STDIN_FILENO, old_settings);
    termios new_settings = *old_settings;
    new_settings.c_lflag &= static_cast<unsigned>(~(ICANON | ECHO));
    new_settings.c_cc[VMIN] = 0;
    new_settings.c_cc[VTIME] = 0;
    tcsetattr(STDIN_FILENO, TCSANOW, &new_settings);
    old_terminal_settings_ = old_settings;
    terminal_initialized_ = true;
#endif
}

void KeyboardController::restore_input_terminal() {
#ifndef _WIN32
    if (!terminal_initialized_ || old_terminal_settings_ == nullptr) return;
    auto* old_settings = static_cast<termios*>(old_terminal_settings_);
    tcsetattr(STDIN_FILENO, TCSANOW, old_settings);
    delete old_settings;
    old_terminal_settings_ = nullptr;
    terminal_initialized_ = false;
#endif
}

bool KeyboardController::key_pressed() const {
#ifdef _WIN32
    return false;
#else
    if (!isatty(STDIN_FILENO)) return false;
    timeval tv{0, 0};
    fd_set fds;
    FD_ZERO(&fds);
    FD_SET(STDIN_FILENO, &fds);
    return select(STDIN_FILENO + 1, &fds, nullptr, nullptr, &tv) > 0;
#endif
}

char KeyboardController::read_key() const {
    char c = 0;
#ifndef _WIN32
    if (::read(STDIN_FILENO, &c, 1) != 1) return 0;
#endif
    return static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
}

bool KeyboardController::handle_keyboard() {
    if (!key_pressed()) return true;
    const char key = read_key();
    switch (key) {
        case 'q': return false;
        case 'r':
            tune_.team = Team::Red;
            sdr_controller_.select_tune_target(Target::Info);
            break;
        case 'b':
            tune_.team = Team::Blue;
            sdr_controller_.select_tune_target(Target::Info);
            break;
        case '1': sdr_controller_.select_tune_target(Target::Info); break;
        case '2': sdr_controller_.select_tune_target(Target::L1); break;
        case '3': sdr_controller_.select_tune_target(Target::L2); break;
        case '4': sdr_controller_.select_tune_target(Target::L3); break;
        case '5': sdr_controller_.select_tune_target(Target::Info, true, false); break;
        case '6': sdr_controller_.select_tune_target(Target::Info, false, true); break;
        case '7': calibration_.apply_direct_profile(RescueMode::L2); break;
        case '8': calibration_.apply_direct_profile(RescueMode::L3); break;
        case 'm': {
            const bool rescue_on = sdr_controller_.is_info_rescue(Target::Info);
            sdr_controller_.select_tune_target(Target::Info, !rescue_on, false);
            break;
        }
        case 'c':
            if (calibration_.active()) calibration_.cancel_calibration();
            else calibration_.start_calibration(false);
            break;
        case 'f':
            if (calibration_.active()) calibration_.cancel_calibration();
            else calibration_.start_calibration(true);
            break;
        case ']':
        case '}': sdr_controller_.cycle_info_rescue_offset(1); break;
        case '[':
        case '{': sdr_controller_.cycle_info_rescue_offset(-1); break;
        case '+':
        case '=': sdr_controller_.adjust_manual_gain(config::GAIN_STEP_DB); break;
        case '-':
        case '_': sdr_controller_.adjust_manual_gain(-config::GAIN_STEP_DB); break;
        default: break;
    }
    return true;
}

} // namespace sdr_receiver
