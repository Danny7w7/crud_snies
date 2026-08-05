export default function AtmosphereBackground({ blobs = true }) {
  return (
    <div className="pointer-events-none absolute inset-0 overflow-hidden" aria-hidden="true">
      <div className="absolute inset-0 bg-[radial-gradient(circle_at_top_left,rgba(236,84,100,0.16),transparent_32%),radial-gradient(circle_at_top_right,rgba(15,31,58,0.14),transparent_20%),radial-gradient(circle_at_bottom_left,rgba(15,31,58,0.16),transparent_30%),radial-gradient(circle_at_bottom_right,rgba(15,31,58,0.24),transparent_38%),linear-gradient(135deg,#f7f8fa_0%,#eef1f5_48%,#e7ebf1_100%)] dark:bg-[radial-gradient(circle_at_top_left,rgba(236,84,100,0.20),transparent_34%),radial-gradient(circle_at_top_right,rgba(48,86,143,0.16),transparent_22%),radial-gradient(circle_at_bottom_left,rgba(48,86,143,0.18),transparent_32%),radial-gradient(circle_at_bottom_right,rgba(48,86,143,0.24),transparent_42%),linear-gradient(135deg,#070b13_0%,#0b1220_50%,#101827_100%)]" />
      {blobs ? (
        <>
          <div className="absolute -left-32 -top-32 h-[34rem] w-[34rem] rounded-full bg-accent/20 blur-[110px] motion-safe:animate-[pulse_9s_ease-in-out_infinite]" />
          <div className="absolute -bottom-40 -right-24 h-[38rem] w-[38rem] rounded-full bg-[#244c82]/20 blur-[130px] motion-safe:animate-[pulse_11s_ease-in-out_infinite]" />
          <div className="absolute left-1/2 top-1/2 h-72 w-72 -translate-x-1/2 -translate-y-1/2 rounded-full bg-white/50 blur-[100px] dark:bg-white/5" />
        </>
      ) : null}
      <div className="absolute inset-0 opacity-[0.035] [background-image:linear-gradient(rgba(15,31,58,0.5)_1px,transparent_1px),linear-gradient(90deg,rgba(15,31,58,0.5)_1px,transparent_1px)] [background-size:44px_44px] dark:opacity-[0.05]" />
    </div>
  )
}
