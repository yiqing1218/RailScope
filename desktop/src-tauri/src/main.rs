#![cfg_attr(not(debug_assertions), windows_subsystem = "windows")]
fn main() { tauri::Builder::default().run(tauri::generate_context!()).expect("RailScope desktop shell failed"); }
