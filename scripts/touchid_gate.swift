// macOS Touch ID + AES-256-GCM Vault Helper (Swift, stdlib Apple frameworks only).
// Build: swiftc -O scripts/touchid_gate.swift -o .shopper-data/bin/touchid-gate -framework LocalAuthentication -framework Foundation -framework CryptoKit
// Commands:
//   touchid-gate auth <reason>         -> triggers macOS Touch ID (falls back to device passcode), exits 0 if approved
//   touchid-gate encrypt               -> stdin: <key_hex>\n<plaintext>; stdout: base64(nonce+ciphertext+tag)
//   touchid-gate decrypt               -> stdin: <key_hex>\n<base64>;    stdout: plaintext
// The key is read from stdin (never argv) so it does not show up in `ps`.

import Foundation
import LocalAuthentication
import CryptoKit

func fail(_ msg: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((msg + "\n").utf8))
    exit(code)
}

func hexToData(_ hex: String) -> Data? {
    let chars = Array(hex.trimmingCharacters(in: .whitespacesAndNewlines))
    guard chars.count % 2 == 0, chars.count == 64 else { return nil }
    var bytes = [UInt8]()
    bytes.reserveCapacity(32)
    var i = 0
    while i < chars.count {
        guard let b = UInt8(String(chars[i...i+1]), radix: 16) else { return nil }
        bytes.append(b)
        i += 2
    }
    return Data(bytes)
}

func readKeyAndBody() -> (Data, Data) {
    let all = FileHandle.standardInput.readDataToEndOfFile()
    guard let nl = all.firstIndex(of: 0x0A),
          let hex = String(data: all[all.startIndex..<nl], encoding: .utf8),
          let key = hexToData(hex) else { fail("stdin must start with a 64-char hex key line") }
    return (key, Data(all[all.index(after: nl)...]))
}

let args = CommandLine.arguments
guard args.count >= 2 else { fail("usage: touchid-gate <auth|encrypt|decrypt> ...") }

switch args[1] {
case "auth":
    let reason = args.count >= 3 ? args[2] : "Unlock encrypted shopping vault (address & password)"
    let ctx = LAContext()
    var err: NSError?
    let policy: LAPolicy = ctx.canEvaluatePolicy(.deviceOwnerAuthenticationWithBiometrics, error: &err)
        ? .deviceOwnerAuthenticationWithBiometrics
        : .deviceOwnerAuthentication
    guard ctx.canEvaluatePolicy(policy, error: &err) else {
        fail("Touch ID / device authentication unavailable: \(err?.localizedDescription ?? "unknown")", code: 2)
    }
    let sema = DispatchSemaphore(value: 0)
    var ok = false
    var authErr: String?
    ctx.evaluatePolicy(policy, localizedReason: reason) { success, error in
        ok = success
        authErr = error?.localizedDescription
        sema.signal()
    }
    sema.wait()
    if ok {
        print("{\"ok\":true,\"method\":\"touchid\"}")
        exit(0)
    } else {
        fail("Touch ID denied: \(authErr ?? "canceled")", code: 3)
    }

case "encrypt":
    let (keyData, plain) = readKeyAndBody()
    let key = SymmetricKey(data: keyData)
    do {
        let sealed = try AES.GCM.seal(plain, using: key)
        guard let combined = sealed.combined else { fail("seal failed") }
        FileHandle.standardOutput.write(Data(combined.base64EncodedString().utf8))
    } catch {
        fail("encrypt failed: \(error.localizedDescription)")
    }

case "decrypt":
    let (keyData, body) = readKeyAndBody()
    let b64 = String(data: body, encoding: .utf8)?
        .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
    guard let combined = Data(base64Encoded: b64) else { fail("invalid base64") }
    let key = SymmetricKey(data: keyData)
    do {
        let box = try AES.GCM.SealedBox(combined: combined)
        let plain = try AES.GCM.open(box, using: key)
        FileHandle.standardOutput.write(plain)
    } catch {
        fail("decrypt failed (wrong key or tampered): \(error.localizedDescription)")
    }

default:
    fail("unknown command: \(args[1])")
}
