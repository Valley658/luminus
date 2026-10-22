use std::os::windows::{io::AsRawHandle, process::CommandExt};
use std::{
    io::Write,
    path::Path,
    process::{Command, Stdio},
};
use windows_sys::Win32::{
    Foundation::{CloseHandle, HANDLE},
    System::JobObjects::*,
};
struct Job(HANDLE);
impl Drop for Job {
    fn drop(&mut self) {
        unsafe {
            CloseHandle(self.0);
        }
    }
}
fn attach(child: &std::process::Child) -> std::io::Result<Job> {
    unsafe {
        let handle = CreateJobObjectW(std::ptr::null(), std::ptr::null());
        if handle.is_null() {
            return Err(std::io::Error::last_os_error());
        }
        let job = Job(handle);
        let memory = std::env::var("LUMINOUS_MEMORY_MB")
            .ok()
            .and_then(|s| s.parse::<usize>().ok())
            .filter(|n| (128..=2048).contains(n))
            .unwrap_or(512);
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        limits.BasicLimitInformation.LimitFlags =
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_JOB_MEMORY;
        limits.JobMemoryLimit = memory * 1024 * 1024;
        if SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        ) == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        let cpu = std::env::var("LUMINOUS_CPU_PERCENT")
            .ok()
            .and_then(|s| s.parse::<u32>().ok())
            .filter(|n| (1..=25).contains(n))
            .unwrap_or(10);
        let limits = JOBOBJECT_CPU_RATE_CONTROL_INFORMATION {
            ControlFlags: JOB_OBJECT_CPU_RATE_CONTROL_ENABLE | JOB_OBJECT_CPU_RATE_CONTROL_HARD_CAP,
            Anonymous: JOBOBJECT_CPU_RATE_CONTROL_INFORMATION_0 { CpuRate: cpu * 100 },
        };
        if SetInformationJobObject(
            handle,
            JobObjectCpuRateControlInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        ) == 0
            || AssignProcessToJobObject(handle, child.as_raw_handle()) == 0
        {
            return Err(std::io::Error::last_os_error());
        }
        Ok(job)
    }
}
pub fn run(root: &Path) -> std::io::Result<()> {
    let mut child = Command::new(std::env::current_exe()?)
        .arg("--serve")
        .current_dir(root)
        .env("PROJECT_DIR", root)
        // Keep the child attached to the current console, with low priority.
        .creation_flags(0x00004000)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()?;
    let job = match attach(&child) {
        Ok(job) => job,
        Err(e) => {
            let _ = child.kill();
            let _ = child.wait();
            return Err(e);
        }
    };
    let log_path = root.join("logs/service.log");
    let _ = std::fs::create_dir_all(root.join("logs"));
    let log_lock = std::sync::Arc::new(std::sync::Mutex::new(()));
    let stdout = pipe_log(
        child.stdout.take().unwrap(),
        log_path.clone(),
        log_lock.clone(),
        false,
    );
    let stderr = pipe_log(child.stderr.take().unwrap(), log_path, log_lock, true);
    if let Err(e) = child.stdin.take().unwrap().write_all(b"ready\n") {
        drop(job);
        let _ = child.wait();
        return Err(e);
    }
    let status = child.wait()?;
    drop(job);
    let _ = stdout.join();
    let _ = stderr.join();
    if status.success() {
        Ok(())
    } else {
        Err(std::io::Error::other(format!(
            "luminus server exited: {status}"
        )))
    }
}
fn pipe_log<R: std::io::Read + Send + 'static>(
    mut input: R,
    path: std::path::PathBuf,
    lock: std::sync::Arc<std::sync::Mutex<()>>,
    stderr: bool,
) -> std::thread::JoinHandle<()> {
    std::thread::spawn(move || {
        let mut bytes = [0u8; 4096];
        while let Ok(n) = input.read(&mut bytes) {
            if n == 0 {
                break;
            }
            if stderr {
                let _ = std::io::stderr().write_all(&bytes[..n]);
            } else {
                let _ = std::io::stdout().write_all(&bytes[..n]);
            }
            if let Ok(_guard) = lock.lock() {
                if std::fs::metadata(&path)
                    .map(|m| m.len() >= 1024 * 1024)
                    .unwrap_or(false)
                {
                    let _ = std::fs::rename(&path, path.with_extension("log.1"));
                    // Windows cannot replace an existing rotation with rename.
                    if std::fs::metadata(&path)
                        .map(|m| m.len() >= 1024 * 1024)
                        .unwrap_or(false)
                    {
                        let _ = std::fs::remove_file(path.with_extension("log.1"));
                        let _ = std::fs::rename(&path, path.with_extension("log.1"));
                    }
                }
                if let Ok(mut file) = std::fs::OpenOptions::new()
                    .create(true)
                    .append(true)
                    .open(&path)
                {
                    let _ = file.write_all(&bytes[..n]);
                }
            }
        }
    })
}
pub fn disk_bytes(root: &Path) -> (u64, u64) {
    use std::os::windows::ffi::OsStrExt;
    let path: Vec<u16> = root.as_os_str().encode_wide().chain(Some(0)).collect();
    let mut available = 0;
    let mut total = 0;
    let mut free = 0;
    unsafe {
        if windows_sys::Win32::Storage::FileSystem::GetDiskFreeSpaceExW(
            path.as_ptr(),
            &mut available,
            &mut total,
            &mut free,
        ) == 0
        {
            return (0, 0);
        }
    }
    (total, available)
}
pub fn memory_bytes() -> (u64, u64) {
    unsafe {
        use windows_sys::Win32::System::SystemInformation::*;
        let mut status: MEMORYSTATUSEX = std::mem::zeroed();
        status.dwLength = std::mem::size_of_val(&status) as u32;
        if GlobalMemoryStatusEx(&mut status) == 0 {
            return (0, 0);
        }
        (status.ullTotalPhys, status.ullAvailPhys)
    }
}
pub fn cpu_usage_percent() -> Option<f64> {
    fn sample() -> Option<(u64,u64,u64)> {
        unsafe {
            let mut idle = std::mem::zeroed();
            let mut kernel = std::mem::zeroed();
            let mut user = std::mem::zeroed();
            if windows_sys::Win32::System::Threading::GetSystemTimes(&mut idle, &mut kernel, &mut user) == 0 { return None; }
            let ticks = |t: windows_sys::Win32::Foundation::FILETIME| (u64::from(t.dwHighDateTime)<<32)|u64::from(t.dwLowDateTime);
            Some((ticks(idle),ticks(kernel),ticks(user)))
        }
    }
    let a=sample()?;
    std::thread::sleep(std::time::Duration::from_millis(250));
    let b=sample()?;
    let total=b.1.saturating_sub(a.1)+b.2.saturating_sub(a.2);
    (total>0).then(|| (100.0*(1.0-b.0.saturating_sub(a.0) as f64/total as f64)).clamp(0.0,100.0))
}
pub fn job_limits() -> (u32, usize) {
    unsafe {
        let mut cpu: JOBOBJECT_CPU_RATE_CONTROL_INFORMATION = std::mem::zeroed();
        let mut memory: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
        if QueryInformationJobObject(
            std::ptr::null_mut(),
            JobObjectCpuRateControlInformation,
            &mut cpu as *mut _ as *mut _,
            std::mem::size_of_val(&cpu) as u32,
            std::ptr::null_mut(),
        ) == 0
            || QueryInformationJobObject(
                std::ptr::null_mut(),
                JobObjectExtendedLimitInformation,
                &mut memory as *mut _ as *mut _,
                std::mem::size_of_val(&memory) as u32,
                std::ptr::null_mut(),
            ) == 0
        {
            return (0, 0);
        }
        (
            cpu.Anonymous.CpuRate / 100,
            memory.JobMemoryLimit / 1024 / 1024,
        )
    }
}
