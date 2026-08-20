import { useEffect, useState } from "react";
import type { TickerSuggestion } from "../types";
import { searchUniverse } from "../api";
import { useDebounce } from "../hooks/useDebounce";

interface Props {
  selected: TickerSuggestion | null;
  onSelect: (s: TickerSuggestion | null) => void;
}

export default function SearchBar({ selected, onSelect }: Props) {
  const [query, setQuery] = useState("");
  const [suggestions, setSuggestions] = useState<TickerSuggestion[]>([]);
  const [open, setOpen] = useState(false);
  const debounced = useDebounce(query, 250);

  useEffect(() => {
    let cancelled = false;
    if (!debounced.trim()) {
      setSuggestions([]);
      return;
    }
    searchUniverse(debounced)
      .then((res) => {
        if (!cancelled) setSuggestions(res);
      })
      .catch(() => {
        if (!cancelled) setSuggestions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [debounced]);

  if (selected) {
    return (
      <div className="search-bar">
        <div className="selected-chip">
          <span className="chip-code">{selected.ticker}</span>
          <span className="chip-name">{selected.name}</span>
          <span className="chip-market">{selected.market}</span>
          <button
            type="button"
            className="chip-clear"
            onClick={() => {
              onSelect(null);
              setQuery("");
            }}
          >
            ×
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="search-bar">
      <input
        type="text"
        placeholder="종목명 또는 종목코드로 검색 (예: 삼성전자, 005930)"
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onBlur={() => setTimeout(() => setOpen(false), 150)}
      />
      {open && suggestions.length > 0 && (
        <ul className="suggestion-list">
          {suggestions.map((s) => (
            <li
              key={s.ticker}
              onMouseDown={() => {
                onSelect(s);
                setOpen(false);
              }}
            >
              <span className="chip-code">{s.ticker}</span>
              <span className="chip-name">{s.name}</span>
              <span className="chip-sector">{s.sector}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
