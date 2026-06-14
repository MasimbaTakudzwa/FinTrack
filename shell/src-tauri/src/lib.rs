use std::env;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager, RunEvent, State, WindowEvent};

struct SidecarState {
    port: u16,
    /// Per-launch bearer token required by the sidecar on every non-health
    /// request. Localhost is reachable by every process on the machine (and,
    /// via DNS-rebinding, by malicious web pages), so the random port is not a
    /// security boundary — this token is. Empty when using an external dev
    /// sidecar we didn't spawn (enforcement is then off on both ends).
    token: String,
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

/// Return the path to the bundled PyInstaller-frozen sidecar binary if present.
///
/// In release builds, `tauri.conf.json` bundles `dist/fintrack-sidecar/` as
/// a resource. Because the source path uses `../..`, Tauri escapes those
/// parent refs into `_up_/` segments when it copies files into the app
/// bundle — on macOS, the binary lands at
/// `FinTrack.app/Contents/Resources/_up_/_up_/dist/fintrack-sidecar/fintrack-sidecar`.
/// We never hard-code that `_up_`-decorated path; `app.path().resolve()` with
/// `BaseDirectory::Resource` applies the same encoding internally, so passing
/// the original `../../dist/fintrack-sidecar/fintrack-sidecar` works on all
/// platforms and across Tauri versions.
///
/// In dev (`pnpm tauri dev` / `cargo run`) the resource dir points at the
/// Cargo target tree which has no frozen binary — `resolve()` returns a path
/// that doesn't exist, we detect that via `is_file()`, return `None`, and the
/// caller falls back to the `.venv/bin/python` spawn path. Hot-reload on the
/// Python side stays intact — no need to re-freeze on every edit.
fn find_frozen_sidecar(app: &AppHandle) -> Option<PathBuf> {
    // In debug builds, always prefer the dev venv — even when a contributor has
    // run PyInstaller locally and a `dist/fintrack-sidecar/` exists at repo
    // root, we want `pnpm tauri dev` to use the venv for fast boot + hot
    // reload. The frozen binary boots ~30 s on a populated prod DB (vs ~2 s
    // for the venv). Release builds still use the bundled frozen binary.
    if cfg!(debug_assertions) {
        return None;
    }
    let binary_name = if cfg!(windows) {
        "fintrack-sidecar.exe"
    } else {
        "fintrack-sidecar"
    };
    let rel = format!("../../dist/fintrack-sidecar/{binary_name}");
    let candidate = app.path().resolve(&rel, BaseDirectory::Resource).ok()?;
    if candidate.is_file() {
        Some(candidate)
    } else {
        None
    }
}

/// Stdio handles that send the sidecar's stdout+stderr to a log file in the
/// app log dir. In a bundled `.app` launched from Finder there is no attached
/// terminal, so inherited stdio would discard every migration/uvicorn error.
/// Falls back to inherited stdio if the log dir/file can't be opened.
fn sidecar_log_stdio(app: &AppHandle) -> (Stdio, Stdio) {
    if let Ok(dir) = app.path().app_log_dir() {
        let _ = std::fs::create_dir_all(&dir);
        let path = dir.join("sidecar.log");
        if let Ok(file) = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
        {
            if let Ok(clone) = file.try_clone() {
                eprintln!("[sidecar] logging to {}", path.display());
                return (Stdio::from(file), Stdio::from(clone));
            }
        }
    }
    (Stdio::inherit(), Stdio::inherit())
}

fn spawn_sidecar(app: &AppHandle, port: u16, token: &str) -> std::io::Result<Child> {
    let (out, err) = sidecar_log_stdio(app);
    if let Some(frozen) = find_frozen_sidecar(app) {
        eprintln!("[sidecar] spawning frozen binary: {}", frozen.display());
        // No `current_dir` — let the frozen sidecar fall through to
        // `platformdirs.user_data_dir()` for DB path resolution in prod.
        // The CWD heuristic in `sidecar/config.py` only picks `./fintrack.db`
        // when it sees `pyproject.toml` + `sidecar/` in CWD, so inheriting
        // whatever the OS gives us (likely `/` on macOS launchd) is correct.
        Command::new(&frozen)
            .env("FINTRACK_PORT", port.to_string())
            .env("FINTRACK_AUTH_TOKEN", token)
            .stdout(out)
            .stderr(err)
            .spawn()
    } else {
        let root = repo_root();
        eprintln!(
            "[sidecar] frozen binary not bundled — falling back to dev venv at {}",
            root.display()
        );
        Command::new(venv_python(&root))
            .args(["-m", "sidecar.main"])
            .current_dir(&root)
            .env("FINTRACK_PORT", port.to_string())
            .env("FINTRACK_AUTH_TOKEN", token)
            .stdout(out)
            .stderr(err)
            .spawn()
    }
}

/// Spawn the sidecar, retrying with a fresh port if the chosen one fails to
/// come up healthy. Closes the TOCTOU window where another process grabs the
/// port between `pick_free_port`'s `drop(listener)` and the child re-binding.
fn start_sidecar(app: &AppHandle, token: &str) -> (u16, Option<Child>) {
    const ATTEMPTS: u32 = 3;
    for attempt in 1..=ATTEMPTS {
        let port = match pick_free_port() {
            Ok(p) => p,
            Err(e) => {
                eprintln!("[sidecar] failed to pick free port: {e}");
                continue;
            }
        };
        match spawn_sidecar(app, port, token) {
            Ok(child) => {
                eprintln!("[sidecar] spawned pid {} on port {}", child.id(), port);
                if wait_for_health(port, Duration::from_secs(10)) {
                    eprintln!("[sidecar] healthy on port {port}");
                    return (port, Some(child));
                }
                eprintln!(
                    "[sidecar] health check timed out on port {port} (attempt {attempt}/{ATTEMPTS})"
                );
                let mut child = child;
                let _ = child.kill();
                let _ = child.wait();
            }
            Err(e) => eprintln!("[sidecar] failed to spawn (attempt {attempt}/{ATTEMPTS}): {e}"),
        }
    }
    eprintln!("[sidecar] gave up after {ATTEMPTS} attempts");
    (0u16, None)
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

#[tauri::command]
fn get_sidecar_token(state: State<'_, SidecarState>) -> String {
    state.token.clone()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_notification::init())
        .plugin(tauri_plugin_updater::Builder::new().build())
        .setup(|app| {
            let external = env::var("FINTRACK_EXTERNAL_SIDECAR").unwrap_or_default() == "1";
            // An external dev sidecar we didn't spawn won't know our token, so
            // leave it empty (sidecar enforcement is off when the env var is
            // unset). Otherwise mint a fresh unguessable token per launch.
            let (port, token, child) = if external {
                eprintln!("[sidecar] using external sidecar on port 8765");
                (
                    8765u16,
                    env::var("FINTRACK_AUTH_TOKEN").unwrap_or_default(),
                    None,
                )
            } else {
                let token = uuid::Uuid::new_v4().simple().to_string();
                let (port, child) = start_sidecar(&app.handle(), &token);
                (port, token, child)
            };

            app.manage(SidecarState {
                port,
                token,
                child: Mutex::new(child),
            });

            let app_handle = app.handle().clone();
            if let Err(e) = ctrlc::set_handler(move || {
                eprintln!("[sidecar] received termination signal, exiting");
                app_handle.exit(0);
            }) {
                eprintln!("[sidecar] failed to install signal handler: {e}");
            }

            Ok(())
        })
        .on_window_event(|window, event| {
            if let WindowEvent::CloseRequested { .. } = event {
                window.app_handle().exit(0);
            }
        })
        .invoke_handler(tauri::generate_handler![get_sidecar_port, get_sidecar_token])
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
