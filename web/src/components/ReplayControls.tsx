interface ReplayControlsProps {
  current: number;
  total: number;
  playing: boolean;
  speed: number;
  hasMore: boolean;
  loadingMore: boolean;
  onCurrentChange: (value: number) => void;
  onPlayingChange: (value: boolean) => void;
  onSpeedChange: (value: number) => void;
  onLoadMore: () => void;
}

const speeds = [1, 2, 4];

export function ReplayControls({
  current,
  total,
  playing,
  speed,
  hasMore,
  loadingMore,
  onCurrentChange,
  onPlayingChange,
  onSpeedChange,
  onLoadMore,
}: ReplayControlsProps) {
  const maximum = Math.max(0, total - 1);
  return (
    <section className="replay-controls" aria-label="Historical replay controls">
      <button
        className="play-button"
        type="button"
        onClick={() => onPlayingChange(!playing)}
        disabled={total === 0}
        aria-label={playing ? "Pause replay" : "Play replay"}
      >
        {playing ? "Ⅱ" : "▶"}
      </button>
      <div className="timeline-control">
        <div className="timeline-labels">
          <span>Deterministic replay</span>
          <strong>
            {total === 0 ? 0 : current + 1} / {total}
          </strong>
        </div>
        <input
          aria-label="Replay position"
          type="range"
          min={0}
          max={maximum}
          value={Math.min(current, maximum)}
          disabled={total === 0}
          onChange={(event) => onCurrentChange(Number(event.target.value))}
        />
      </div>
      <fieldset className="speed-control" aria-label="Replay speed">
        {speeds.map((value) => (
          <button
            className={speed === value ? "active" : ""}
            key={value}
            type="button"
            onClick={() => onSpeedChange(value)}
          >
            {value}×
          </button>
        ))}
      </fieldset>
      {hasMore && (
        <button className="load-more" type="button" onClick={onLoadMore} disabled={loadingMore}>
          {loadingMore ? "Loading…" : "Load 500 more"}
        </button>
      )}
    </section>
  );
}
