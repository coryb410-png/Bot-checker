// MADBOY marks. Faces are white-on-transparent PNGs in /public/brand.
// Moods: mad = something went wrong or a loss · shh = secrets, quiet work, unknowns
//        smirk = neutral or done · cash = money and gains
export type Mood = "mad" | "smirk" | "cash" | "shh";

export function moodFor(n: number | null | undefined): Mood {
  if (n === null || n === undefined || Number.isNaN(n)) return "shh";
  return n > 0.004 ? "cash" : n < -0.004 ? "mad" : "smirk";
}

/** A monkey beside a sentence. Use for every message, empty state and status. */
export function Say({ mood, children, size = "sm", className, role }: {
  mood: Mood; children: React.ReactNode; size?: "xs" | "sm" | "md" | "lg"; className?: string; role?: string;
}) {
  return (
    <div className={`mood ${className ?? ""}`} role={role}>
      <Monkey mood={mood} size={size} />
      <div style={{ minWidth: 0 }}>{children}</div>
    </div>
  );
}

export function Monkey({ mood, size = "md", bare = false, label }: { mood: Mood; size?: "xs" | "sm" | "md" | "lg" | "xl"; bare?: boolean; label?: string }) {
  return (
    <span
      className={`mk ${size} ${bare ? "bare" : ""}`}
      style={{ backgroundImage: `url(/brand/${mood}.png)` }}
      role={label ? "img" : undefined}
      aria-label={label}
      aria-hidden={label ? undefined : true}
    />
  );
}

export function BrandMark() {
  return (
    <span className="brandmark">
      <Monkey mood="mad" bare label="MADBOY monkey logo" />
      <span>
        <b>ChainScope</b>
        <small>by <span className="wordmark" role="img" aria-label="MADBOY" /></small>
      </span>
    </span>
  );
}
