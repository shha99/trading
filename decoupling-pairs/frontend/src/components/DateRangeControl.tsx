import { PRESET_LABELS, Preset, presetRange } from "../dateUtils";

interface Props {
  start: string;
  end: string;
  asOf: string | null;
  onChange: (start: string, end: string) => void;
}

const PRESETS: Preset[] = ["1m", "3m", "6m", "1y"];

export default function DateRangeControl({ start, end, asOf, onChange }: Props) {
  return (
    <div className="date-range-control">
      <div className="preset-row">
        {PRESETS.map((p) => (
          <button
            key={p}
            type="button"
            className="preset-btn"
            onClick={() => {
              const r = presetRange(p, asOf);
              onChange(r.start, r.end);
            }}
          >
            {PRESET_LABELS[p]}
          </button>
        ))}
      </div>
      <div className="date-inputs">
        <label>
          시작일
          <input type="date" value={start} onChange={(e) => onChange(e.target.value, end)} />
        </label>
        <label>
          종료일
          <input type="date" value={end} onChange={(e) => onChange(start, e.target.value)} />
        </label>
      </div>
    </div>
  );
}
