import Foundation
import Darwin
import SQLite3

final class LocalThreadCatalog {
    private struct Identity: Equatable {
        let device: dev_t
        let inode: ino_t
    }
    private enum Node: Equatable {
        case absent, directory(Identity), file(Identity), unavailable
    }
    private struct Catalog: Equatable {
        let home: Identity
        let database: Identity
        let schema: Int64
        let version: Int64
    }
    private struct Evidence {
        let catalog: Catalog
        let roots: [String: Node]
        let paths: [URL]
    }
    private let configuredHome: URL
    private let home: URL
    private var evidence: [String: Evidence] = [:]

    init(home: URL) {
        configuredHome = home.standardizedFileURL
        self.home = configuredHome.resolvingSymlinksInPath()
    }

    func remember(_ id: String) {
        guard evidence[id] == nil, let (catalog, path) = read(id), let path,
              let paths = rolloutPaths(path),
              [fileNode(paths[0]), fileNode(paths[1])].contains(true),
              let roots = storageRoots() else { return }
        evidence[id] = Evidence(catalog: catalog, roots: roots, paths: paths)
    }

    func forget(_ id: String) { evidence.removeValue(forKey: id) }

    func isOrphan(_ id: String) -> Bool {
        guard let saved = evidence[id], let (catalog, path) = read(id),
              catalog == saved.catalog, path == nil,
              rootsAvailable(saved.roots), inactive(id),
              variantsMissing(saved.paths),
              let (latest, latestPath) = read(id), latest == saved.catalog,
              latestPath == nil, rootsAvailable(saved.roots), inactive(id),
              variantsMissing(saved.paths) else { return false }
        return true
    }

    private func variantsMissing(_ paths: [URL]) -> Bool {
        paths.allSatisfy {
            $0.deletingLastPathComponent().resolvingSymlinksInPath().path == $0.deletingLastPathComponent().path
                && node($0) == .absent
        }
    }

    private func inactive(_ id: String) -> Bool {
        let locks = home.appendingPathComponent("thread-writer-locks")
        let migrations = home.appendingPathComponent("rollout-migrations")
        guard case .directory = node(locks), access(locks.path, R_OK | X_OK) == 0 else { return false }
        switch node(migrations) {
        case .directory:
            guard access(migrations.path, R_OK | X_OK) == 0 else { return false }
        case .absent: break
        default: return false
        }
        return node(locks.appendingPathComponent("\(id).lock")) == .absent
            && node(migrations.appendingPathComponent("\(id).pending")) == .absent
    }

    private func storageRoots() -> [String: Node]? {
        var result: [String: Node] = [:]
        for name in ["sessions", "archived_sessions"] {
            let path = home.appendingPathComponent(name)
            let status = node(path)
            switch status {
            case .directory:
                guard access(path.path, R_OK | X_OK) == 0 else { return nil }
            case .absent: break
            default: return nil
            }
            result[name] = status
        }
        return result
    }

    private func rootsAvailable(_ saved: [String: Node]) -> Bool {
        guard let current = storageRoots() else { return false }
        return saved.allSatisfy { name, previous in
            if previous == .absent { return true }
            return current[name] == previous
        }
    }

    private func rolloutPaths(_ value: String) -> [URL]? {
        guard value.hasPrefix("/") else { return nil }
        let selected = URL(fileURLWithPath: value).standardizedFileURL
        guard selected.resolvingSymlinksInPath().path == selected.path,
              selected.path.hasPrefix(home.path + "/") else { return nil }
        let relative = Array(selected.pathComponents.dropFirst(home.pathComponents.count))
        guard let name = relative.last else { return nil }
        let plain = name.hasSuffix(".jsonl.zst") ? String(name.dropLast(4)) : name
        guard plain.hasPrefix("rollout-"), plain.hasSuffix(".jsonl") else { return nil }
        let stem = String(plain.dropLast(6))
        let identifiers = stem.split(separator: "_", omittingEmptySubsequences: false)
        guard stem.count >= 55, stem.dropFirst(18).first == "T", (1...2).contains(identifiers.count),
              identifiers[0].dropLast(36).last == "-", UUID(uuidString: String(identifiers[0].suffix(36))) != nil,
              identifiers.count == 1 || (identifiers[1].count == 36 && UUID(uuidString: String(identifiers[1])) != nil) else { return nil }
        let date = String(plain.dropFirst(8).prefix(10))
        guard date.range(of: #"^\d{4}-\d{2}-\d{2}$"#, options: .regularExpression) != nil else { return nil }
        let parts = date.split(separator: "-").map(String.init)
        guard relative == ["sessions"] + parts + [name] || relative == ["archived_sessions", name] else { return nil }
        let active = home.appendingPathComponent((["sessions"] + parts + [plain]).joined(separator: "/"))
        let archived = home.appendingPathComponent("archived_sessions/" + plain)
        let first = selected.deletingLastPathComponent().appendingPathComponent(plain)
        let other = relative.first == "sessions" ? archived : active
        return [first, first.appendingPathExtension("zst"), other, other.appendingPathExtension("zst")]
    }

    private func unambiguousLegacyCatalog() -> Bool {
        let legacy = home.appendingPathComponent("sqlite")
        switch node(legacy) {
        case .absent: return true
        case .directory:
            guard access(legacy.path, R_OK | X_OK) == 0,
                  let names = try? FileManager.default.contentsOfDirectory(atPath: legacy.path) else { return false }
            return !names.contains { $0.hasPrefix("state_") && $0.hasSuffix(".sqlite") }
        default: return false
        }
    }

    private func read(_ id: String) -> (Catalog, String?)? {
        guard UUID(uuidString: id) != nil, configuredHome.resolvingSymlinksInPath().path == home.path,
              case let .directory(homeID) = node(home),
              access(home.path, R_OK | X_OK) == 0,
              let names = try? FileManager.default.contentsOfDirectory(atPath: home.path),
              names.filter({ $0.hasPrefix("state_") && $0.hasSuffix(".sqlite") }) == ["state_5.sqlite"],
              unambiguousLegacyCatalog() else { return nil }
        let path = home.appendingPathComponent("state_5.sqlite")
        guard case let .file(databaseID) = node(path) else { return nil }
        var connection: OpaquePointer?
        guard sqlite3_open_v2(path.path, &connection, SQLITE_OPEN_READONLY | SQLITE_OPEN_NOMUTEX, nil) == SQLITE_OK,
              let db = connection else { if let connection { sqlite3_close(connection) }; return nil }
        defer { sqlite3_close(db) }
        sqlite3_busy_timeout(db, 0)
        guard sqlite3_exec(db, "PRAGMA query_only = ON", nil, nil, nil) == SQLITE_OK,
              sqlite3_exec(db, "BEGIN", nil, nil, nil) == SQLITE_OK else { return nil }
        defer { sqlite3_exec(db, "ROLLBACK", nil, nil, nil) }
        guard let schema = integer(db, "PRAGMA schema_version"),
              let version = integer(db, "PRAGMA user_version"), validColumns(db) else { return nil }
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT rollout_path FROM threads WHERE id = ?", -1, &statement, nil) == SQLITE_OK,
              let query = statement else { return nil }
        defer { sqlite3_finalize(query) }
        let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)
        guard sqlite3_bind_text(query, 1, id, -1, transient) == SQLITE_OK else { return nil }
        let result = sqlite3_step(query)
        let rollout: String?
        if result == SQLITE_ROW {
            guard sqlite3_column_type(query, 0) == SQLITE_TEXT, let text = sqlite3_column_text(query, 0) else { return nil }
            let length = Int(sqlite3_column_bytes(query, 0))
            guard length > 0, length < Int(PATH_MAX) else { return nil }
            let bytes = UnsafeBufferPointer(start: text, count: length)
            guard !bytes.contains(0), let value = String(bytes: bytes, encoding: .utf8) else { return nil }
            rollout = value
            guard sqlite3_step(query) == SQLITE_DONE else { return nil }
        } else if result == SQLITE_DONE { rollout = nil }
        else { return nil }
        guard node(home) == .directory(homeID), node(path) == .file(databaseID) else { return nil }
        return (Catalog(home: homeID, database: databaseID, schema: schema, version: version), rollout)
    }

    private func validColumns(_ db: OpaquePointer) -> Bool {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, "PRAGMA table_info(threads)", -1, &statement, nil) == SQLITE_OK,
              let query = statement else { return false }
        defer { sqlite3_finalize(query) }
        var id = false, path = false, primaryColumns = 0
        var result = sqlite3_step(query)
        while result == SQLITE_ROW {
            guard let name = sqlite3_column_text(query, 1), let type = sqlite3_column_text(query, 2) else { return false }
            let column = String(cString: name), kind = String(cString: type).uppercased()
            let primary = sqlite3_column_int(query, 5)
            if primary > 0 { primaryColumns += 1 }
            if column == "id" { id = kind == "TEXT" && primary == 1 }
            if column == "rollout_path" { path = kind == "TEXT" }
            result = sqlite3_step(query)
        }
        return result == SQLITE_DONE && id && path && primaryColumns == 1
    }

    private func integer(_ db: OpaquePointer, _ sql: String) -> Int64? {
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &statement, nil) == SQLITE_OK,
              let query = statement else { return nil }
        defer { sqlite3_finalize(query) }
        guard sqlite3_step(query) == SQLITE_ROW else { return nil }
        let value = sqlite3_column_int64(query, 0)
        return sqlite3_step(query) == SQLITE_DONE ? value : nil
    }

    private func fileNode(_ path: URL) -> Bool {
        if case .file = node(path) { return true }
        return false
    }

    private func node(_ path: URL) -> Node {
        var attributes = stat()
        guard lstat(path.path, &attributes) == 0 else { return errno == ENOENT ? .absent : .unavailable }
        let identity = Identity(device: attributes.st_dev, inode: attributes.st_ino)
        switch attributes.st_mode & S_IFMT {
        case S_IFREG: return .file(identity)
        case S_IFDIR: return .directory(identity)
        default: return .unavailable
        }
    }
}
