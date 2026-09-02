import { useState, useEffect, useRef, useCallback } from "react";
import { api } from "../api/client";
import { useChatStore } from "../stores/chatStore";
import { SearchIcon, CheckCircleIcon, AlertCircleIcon, XIcon, TrendingUpIcon } from "./Icon";

interface StockHit {
  code: string;
  name: string;
  exchange: string;
  board: string;
}

interface AddWatchlistDialogProps {
  onClose: () => void;
}

export default function AddWatchlistDialog({ onClose }: AddWatchlistDialogProps) {
  const [input, setInput] = useState("");
  const [results, setResults] = useState<StockHit[]>([]);
  const [selected, setSelected] = useState<StockHit | null>(null);
  const [error, setError] = useState("");
  const [searching, setSearching] = useState(false);
  const [dropdownOpen, setDropdownOpen] = useState(false);
  const [highlightIdx, setHighlightIdx] = useState(-1);
  const { addToWatchlist, watchlist, watchlistMax } = useChatStore();

  const inputRef = useRef<HTMLInputElement>(null);
  const debounceRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const existingSymbols = new Set(watchlist.map((w) => w.symbol));

  // 防抖搜索
  const doSearch = useCallback(async (q: string) => {
    if (!q.trim() || q.trim().length < 1) {
      setResults([]);
      setDropdownOpen(false);
      return;
    }
    setSearching(true);
    try {
      const res = await api.searchStocks(q.trim(), 8);
      // 精确代码匹配
      const exact = res.results.find((r) => r.code === q.trim());
      if (exact) {
        setSelected(exact);
      }
      setResults(res.results);
      setDropdownOpen(res.results.length > 0);
    } catch {
      setResults([]);
      setDropdownOpen(false);
    } finally {
      setSearching(false);
    }
  }, []);

  useEffect(() => {
    if (debounceRef.current) clearTimeout(debounceRef.current);
    if (input.trim().length >= 1) {
      debounceRef.current = setTimeout(() => doSearch(input), 200);
    } else {
      setResults([]);
      setDropdownOpen(false);
      setSelected(null);
    }
    return () => { if (debounceRef.current) clearTimeout(debounceRef.current); };
  }, [input, doSearch]);

  // 结果更新时精确匹配
  useEffect(() => {
    if (results.length === 0) return;
    const exact = results.find((r) => r.code === input.trim());
    if (exact && (!selected || selected.code !== exact.code)) {
      setSelected(exact);
      setError("");
    }
  }, [results, input, selected]);

  const handleSelect = (hit: StockHit) => {
    setSelected(hit);
    setInput(hit.name);
    setDropdownOpen(false);
    setHighlightIdx(-1);
    setError("");
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (!dropdownOpen || results.length === 0) {
      if (e.key === "Enter" && selected) {
        handleAdd();
      }
      return;
    }
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setHighlightIdx((prev) => (prev + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setHighlightIdx((prev) => (prev - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      if (highlightIdx >= 0 && highlightIdx < results.length) {
        handleSelect(results[highlightIdx]);
      } else if (selected) {
        handleAdd();
      }
    } else if (e.key === "Escape") {
      setDropdownOpen(false);
      setHighlightIdx(-1);
    }
  };

  const handleAdd = async () => {
    if (!selected) return;
    if (existingSymbols.has(selected.code)) {
      setError(`${selected.name}(${selected.code}) 已在自选股列表中`);
      return;
    }
    if (watchlist.length >= watchlistMax) {
      setError(`自选股最多${watchlistMax}只，请先移除其他股票`);
      return;
    }
    try {
      await addToWatchlist(selected.code, selected.name);
      onClose();
    } catch (err: any) {
      setError(err.message || "添加失败");
    }
  };

  const exchangeLabel = (ex: string) => {
    if (ex === "sh") return "沪";
    if (ex === "sz") return "深";
    if (ex === "bj") return "京";
    return ex;
  };
  const exchangeColor = (ex: string) => {
    if (ex === "sh") return "text-red-400 bg-red-400/10 border-red-400/20";
    if (ex === "sz") return "text-green-400 bg-green-400/10 border-green-400/20";
    if (ex === "bj") return "text-amber-400 bg-amber-400/10 border-amber-400/20";
    return "text-text-muted bg-surface-secondary border-border";
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 backdrop-blur-sm backdrop-enter"
      onClick={onClose}
      role="dialog"
      aria-modal="true"
      aria-label="添加自选股"
    >
      <div
        className="glass-strong rounded-2xl border border-border shadow-2xl p-5 w-[420px] max-w-[92vw] dialog-enter"
        style={{ boxShadow: "0 24px 60px rgba(0,0,0,0.45)" }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-1">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 rounded-lg bg-accent-glow text-accent border border-accent/20 flex items-center justify-center">
              <TrendingUpIcon size={16} />
            </div>
            <h3 className="text-base font-bold text-text-primary">添加自选股</h3>
          </div>
          <button
            onClick={onClose}
            aria-label="关闭"
            className="icon-btn w-7 h-7 rounded-lg text-text-muted hover:text-text-primary hover:bg-elevated"
          >
            <XIcon size={16} />
          </button>
        </div>
        <p className="text-xs text-text-muted mb-4 ml-9">输入股票代码或名称关键字，自动匹配并添加</p>

        {/* 搜索输入 */}
        <div className="relative mb-3">
          <div className="relative">
            <SearchIcon size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-text-muted z-10" />
            <input
              ref={inputRef}
              type="text"
              value={input}
              onChange={(e) => { setInput(e.target.value); setError(""); }}
              onKeyDown={handleKeyDown}
              onFocus={() => { if (results.length > 0) setDropdownOpen(true); }}
              onBlur={() => setTimeout(() => setDropdownOpen(false), 200)}
              placeholder="输入代码或名称，如 600519 / 茅台"
              className="w-full pl-9 pr-3 py-2.5 text-sm rounded-xl bg-input-bg border border-input-border text-text-primary placeholder:text-text-muted focus:outline-none focus:border-accent focus:ring-1 focus:ring-accent/30 transition-all"
              autoFocus
            />
            {searching && (
              <div className="absolute right-3 top-1/2 -translate-y-1/2">
                <div className="w-4 h-4 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />
              </div>
            )}
          </div>

          {/* 下拉结果 */}
          {dropdownOpen && results.length > 0 && (
            <div className="absolute left-0 right-0 top-full mt-1 bg-surface-primary border border-border rounded-xl shadow-2xl z-50 max-h-[300px] overflow-y-auto scrollbar-thin">
              {results.map((hit, idx) => {
                const isHighlighted = idx === highlightIdx;
                const isSelected = selected?.code === hit.code;
                return (
                  <div
                    key={hit.code}
                    onMouseDown={() => handleSelect(hit)}
                    onMouseEnter={() => setHighlightIdx(idx)}
                    className={`flex items-center gap-3 px-3 py-2.5 cursor-pointer transition-colors ${
                      isHighlighted
                        ? "bg-accent-glow"
                        : isSelected
                        ? "bg-accent-soft"
                        : "hover:bg-hover"
                    }`}
                  >
                    <span className="text-sm font-medium text-text-primary flex-1">{hit.name}</span>
                    <span className="text-xs font-data text-text-muted">{hit.code}</span>
                    <span className={`text-[11px] px-1.5 py-0.5 rounded font-medium border ${exchangeColor(hit.exchange)}`}>
                      {exchangeLabel(hit.exchange)}
                    </span>
                    <span className="text-[11px] text-text-muted">{hit.board}</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>

        {/* 已选确认 */}
        {selected && (
          <div className="bg-accent-soft border border-accent/20 rounded-xl px-3 py-3 mb-3 animate-fade-in">
            <div className="flex items-center gap-2">
              <CheckCircleIcon size={16} className="text-accent flex-shrink-0" />
              <span className="text-sm font-semibold text-text-primary">{selected.name}</span>
              <span className="text-xs text-text-muted font-data ml-auto">{selected.code}</span>
              <span className={`text-[11px] px-1.5 py-0.5 rounded font-medium border ${exchangeColor(selected.exchange)}`}>
                {exchangeLabel(selected.exchange)}
              </span>
            </div>
            <div className="text-xs text-accent ml-6 mt-1">
              {selected.board} · 已选择
            </div>
          </div>
        )}

        {/* 错误提示 */}
        {error && (
          <div className="flex items-start gap-2 text-xs text-danger bg-danger-soft border border-danger/20 rounded-xl px-3 py-2.5 mb-3 animate-fade-in">
            <AlertCircleIcon size={14} className="flex-shrink-0 mt-0.5" />
            <span>{error}</span>
          </div>
        )}

        {/* 操作按钮 */}
        <div className="flex gap-2 justify-end">
          <button
            onClick={onClose}
            className="px-4 py-2 text-sm text-text-secondary bg-input-bg border border-input-border rounded-xl hover:bg-elevated active:bg-elevated-hover transition-colors"
          >
            取消
          </button>
          <button
            onClick={handleAdd}
            disabled={!selected || existingSymbols.has(selected.code)}
            className="btn btn-primary px-4 py-2 text-sm rounded-xl"
          >
            添加到自选
          </button>
        </div>
      </div>
    </div>
  );
}
