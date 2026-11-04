// BUCS.app main executable: a small native Cocoa shell around the Python launcher.
//
// * starts Resources/python/bin/python3 Resources/launcher/bucs_launcher.py, which installs the
//   app files into ~/Library/Application Support/BUCS (writable: in-app updates, user data),
//   runs the server with the restart/update/rollback loop and opens the dashboard window;
// * gives BUCS a Dock icon and menu (Open Dashboard, Show Logs, Quit); clicking the Dock icon
//   reopens the dashboard window;
// * Quit (menu, Cmd-Q, Dock, logout) stops the server gracefully (SIGTERM -> launcher -> SIGINT
//   to uvicorn) before the app exits; when the server is shut down from the dashboard the app
//   quits too.
//
// Build: swiftc -O -target arm64-apple-macos11.0 main.swift -o BUCS   (x86_64-apple-macos11.0 for Intel)

import Cocoa

final class AppDelegate: NSObject, NSApplicationDelegate {
    private var launcher: Process?
    private var quitting = false
    private let res = Bundle.main.resourcePath ?? "."
    private let home = NSHomeDirectory() + "/Library/Application Support/BUCS"

    private var python: String { res + "/python/bin/python3" }
    private var launcherScript: String { res + "/launcher/bucs_launcher.py" }

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        startLauncher()
    }

    private func environment(openWindow: Bool = false) -> [String: String] {
        var env = ProcessInfo.processInfo.environment
        env["PYTHONPYCACHEPREFIX"] = home + "/.pycache"   // never write .pyc files into the signed bundle
        env["PYTHONNOUSERSITE"] = "1"
        env.removeValue(forKey: "PYTHONHOME")
        env["PATH"] = (env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin") + ":/opt/homebrew/bin:/usr/local/bin"
        if openWindow { env.removeValue(forKey: "BUCS_NO_BROWSER") }   // the user asked for the window
        return env
    }

    private func startLauncher() {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-s", "-B", launcherScript, "--home", home, "--bundle", res + "/app"]
        p.environment = environment()
        p.terminationHandler = { proc in
            let code = proc.terminationStatus
            DispatchQueue.main.async { self.launcherExited(code) }
        }
        do {
            try p.run()
            launcher = p
        } catch {
            alert("BUCS could not start", "The bundled Python runtime failed to launch: \(error.localizedDescription)")
            NSApp.terminate(nil)
        }
    }

    private func launcherExited(_ code: Int32) {
        launcher = nil
        if quitting {
            NSApp.reply(toApplicationShouldTerminate: true)
            return
        }
        // 0 = another BUCS was already running (it opened its window) or a clean stop,
        // 99 = shut down from the dashboard; anything else is a failure worth showing
        if code != 0 && code != 99 {
            alert("BUCS stopped unexpectedly",
                  "Details are in ~/Library/Application Support/BUCS/logs (server.log, launcher.log).")
        }
        NSApp.terminate(nil)
    }

    @objc func openDashboard(_ sender: Any?) {
        guard launcher != nil else { startLauncher(); return }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = ["-s", "-B", launcherScript, "--home", home, "--open"]
        p.environment = environment(openWindow: true)
        try? p.run()
    }

    @objc func showLogs(_ sender: Any?) {
        NSWorkspace.shared.open(URL(fileURLWithPath: home + "/logs"))
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        openDashboard(nil)
        return true
    }

    func applicationDockMenu(_ sender: NSApplication) -> NSMenu? {
        let m = NSMenu()
        m.addItem(NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard(_:)), keyEquivalent: ""))
        m.addItem(NSMenuItem(title: "Show Logs", action: #selector(showLogs(_:)), keyEquivalent: ""))
        return m
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let p = launcher, p.isRunning else { return .terminateNow }
        quitting = true
        kill(p.processIdentifier, SIGTERM)                 // launcher stops the server gracefully
        DispatchQueue.main.asyncAfter(deadline: .now() + 30) {
            if self.launcher != nil {                       // still alive: don't hang the logout
                kill(p.processIdentifier, SIGKILL)
                NSApp.reply(toApplicationShouldTerminate: true)
            }
        }
        return .terminateLater
    }

    private func alert(_ title: String, _ text: String) {
        NSApp.activate(ignoringOtherApps: true)
        let a = NSAlert()
        a.messageText = title
        a.informativeText = text
        a.alertStyle = .warning
        a.runModal()
    }

    private func buildMenu() {
        let main = NSMenu()
        let appItem = NSMenuItem()
        main.addItem(appItem)
        let appMenu = NSMenu(title: "BUCS")
        appMenu.addItem(NSMenuItem(title: "About BUCS",
                                   action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: ""))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Open Dashboard", action: #selector(openDashboard(_:)), keyEquivalent: "o"))
        appMenu.addItem(NSMenuItem(title: "Show Logs", action: #selector(showLogs(_:)), keyEquivalent: "l"))
        appMenu.addItem(.separator())
        appMenu.addItem(NSMenuItem(title: "Hide BUCS", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h"))
        appMenu.addItem(NSMenuItem(title: "Quit BUCS", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q"))
        appItem.submenu = appMenu
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
