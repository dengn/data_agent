import { useState, useEffect, useCallback, useRef } from "react";
import {
  listSources,
  uploadFile,
  deleteSource,
  type DataSourceInfo,
} from "../api";

export default function Sidebar() {
  const [sources, setSources] = useState<DataSourceInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const refresh = useCallback(async () => {
    try {
      setSources(await listSources());
    } catch {
      /* ignore on first load */
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  async function handleUpload(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setError("");
    try {
      await uploadFile(file);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  async function handleDelete(id: string, name: string) {
    if (!confirm(`确定删除数据源「${name}」？`)) return;
    try {
      await deleteSource(id);
      await refresh();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <aside className="sidebar">
      <h1 className="sidebar-title">Data Agent</h1>

      <div className="sidebar-section">
        <h3>数据源</h3>
        <label className={`upload-btn ${uploading ? "uploading" : ""}`}>
          {uploading ? "上传中…" : "+ 上传文件"}
          <input
            ref={fileRef}
            type="file"
            accept=".csv,.xlsx,.xls"
            onChange={handleUpload}
            disabled={uploading}
            hidden
          />
        </label>
        {error && <div className="sidebar-error">{error}</div>}
      </div>

      <ul className="source-list">
        {sources.map((s) => (
          <li key={s.id} className="source-item">
            <div className="source-info">
              <span className="source-name">{s.name}</span>
              <span className="source-type">{s.source_type}</span>
            </div>
            <button
              className="source-delete"
              title="删除"
              onClick={() => handleDelete(s.id, s.name)}
            >
              &times;
            </button>
          </li>
        ))}
        {sources.length === 0 && (
          <li className="source-empty">暂无数据源，请上传文件</li>
        )}
      </ul>
    </aside>
  );
}
