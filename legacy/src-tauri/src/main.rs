#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]

fn main() {
    // The native desktop chrome is owned by the shell crate.
    empyralis_tauri_shell::run();
}
