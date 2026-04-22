use std::env;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::{Manager, RunEvent, State, WindowEvent};

struct SidecarState {
    port: u16,
    child: Mutex<Option<Child>>,
}

fn pick_free_port() -> std::io::Result<u16> {
    let listener = TcpListener::bind("127.0.0.1:0")?;
    let port = listener.local_addr()?.port();
    drop(listener);
    Ok(port)
}

fn repo_root() -> PathBuf {
    let manifest = env!("CARGO_MANIFEST_DIR");
    PathBuf::from(manifest).join("..").join("..")
}

fn venv_python(root: &Path) -> PathBuf {
    if cfg!(windows) {
        root.join(".venv").join("Scripts").join("python.exe")
    } else {
        root.join(".venv").join("bin").join("python")
    }
}

fn spawn_sidecar(port: u16) -> std::io::Result<Child> {
    let root = repo_root();
    Command::new(venv_python(&root))
        .args(["-m", "sidecar.main"])
        .current_dir(&root)
        .env("FINTRACK_PORT", port.to_string())
        .spawn()
}

fn wait_for_health(port: u16, timeout: Duration) -> bool {
    let url = format!("http://127.0.0.1:{port}/api/health/");
    let agent = ureq::AgentBuilder::new()
        .timeout(Duration::from_millis(400))
        .build();
    let start = Instant::now();
    while start.elapsed() < timeout {
        if let Ok(resp) = agent.get(&url).call() {
            if resp.status() == 200 {
                return true;
            }
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    false
}

#[tauri::command]
fn get_sidecar_port(state: State<'_, SidecarState>) -> u16 {
    state.port
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .setup(|app| {
            let external = env::var("FINTRACK_EXTERNAL_SIDECAR").unwrap_or_default() == "1";
            let (port, child) = if external {
                eprintln!("[sidecar] using external sidecar on port 8765");
                (8765u16, None)
            } else {
                match pick_free_port() {
                    Ok(p) => match spawn_sidecar(p) {
                        Ok(c) => {
                            eprintln!("[sidecar] spawned pid {} on port {}", c.id(), p);
                            (p, Some(c))
                        }
                        Err(e) => {
                            eprintln!("[sidecar] failed to spawn: {e}");
                            (0u16, None)
                        }
                    },
                    Err(e) => {
                        eprintln!("[sidecar] failed to pick free port: {e}");
                        (0u16, None)
                    }
                }
            };

            if port != 0 && wait_for_health(port, Duration::from_secs(10)) {
                eprintln!("[sidecar] healthy on port {port}");
            } else if port != 0 {
                eprintln!("[sidecar] health check timed out on port {port}");
            }

            app.manage(SidecarState {
                port,
                child: Mutex::new(child),
            });
            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                window.app_handle().exit(0);
            }
        })
        .invoke_handler(tauri::generate_handler![get_sidecar_port])
        .build(tauri::generate_context!())
        .expect("error while building tauri application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            if let Some(state) = app_handle.try_state::<SidecarState>() {
                if let Ok(mut guard) = state.child.lock() {
                    if let Some(mut child) = guard.take() {
                        eprintln!("[sidecar] terminating child pid {}", child.id());
                        let _ = child.kill();
                        let _ = child.wait();
                    }
                }
            }
        }
    });
}
