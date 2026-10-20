const base = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.8 };

// Every icon forwards whatever props its caller passes (style, className,
// width/height, aria-hidden, ...) onto the <svg> itself, spread AFTER base/
// the fill default so a caller's own values win. Without this, an icon call
// site passing e.g. style={{width:14,color:'...'}} silently did nothing --
// a real bug hit twice in one session (EmailDraftCard, HomeSection) before
// being fixed here once, at the source, instead of per call site.
export const ChatIcon = (props) => <svg {...base} {...props}><path d="M4 4h16v13H8l-4 4V4z"/></svg>;
export const TaskIcon = (props) => <svg {...base} {...props}><path d="M4 6h16M4 12h16M4 18h10"/><circle cx="20" cy="18" r="1.5" fill="currentColor" stroke="none"/></svg>;
export const ProfileIcon = (props) => <svg {...base} {...props}><circle cx="12" cy="8" r="3.2"/><path d="M5.5 20a6.5 6.5 0 0113 0"/></svg>;
export const FriendIcon = (props) => <svg {...base} {...props}><circle cx="8.5" cy="8" r="3"/><circle cx="17" cy="10" r="2.4"/><path d="M2.5 20a6 6 0 0112 0M13.5 20a5 5 0 018.5-3.5"/></svg>;
export const SettingsIcon = (props) => <svg {...base} {...props}><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 00.34 1.87l.06.06a2 2 0 11-2.83 2.83l-.06-.06a1.7 1.7 0 00-1.87-.34 1.7 1.7 0 00-1 1.55V21a2 2 0 11-4 0v-.09a1.7 1.7 0 00-1-1.55 1.7 1.7 0 00-1.87.34l-.06.06a2 2 0 11-2.83-2.83l.06-.06a1.7 1.7 0 00.34-1.87 1.7 1.7 0 00-1.55-1H3a2 2 0 110-4h.09a1.7 1.7 0 001.55-1 1.7 1.7 0 00-.34-1.87l-.06-.06a2 2 0 112.83-2.83l.06.06a1.7 1.7 0 001.87.34H9a1.7 1.7 0 001-1.55V3a2 2 0 114 0v.09a1.7 1.7 0 001 1.55 1.7 1.7 0 001.87-.34l.06-.06a2 2 0 112.83 2.83l-.06.06a1.7 1.7 0 00-.34 1.87V9a1.7 1.7 0 001.55 1H21a2 2 0 110 4h-.09a1.7 1.7 0 00-1.55 1z"/></svg>;
export const SearchIcon = (props) => <svg {...base} {...props}><circle cx="11" cy="11" r="7"/><path d="M21 21l-4.3-4.3"/></svg>;
export const PencilIcon = (props) => <svg {...base} {...props}><path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4z"/></svg>;
export const BackIcon = (props) => <svg {...base} {...props}><path d="M15 18l-6-6 6-6"/></svg>;
export const InfoIcon = (props) => <svg {...base} {...props}><circle cx="12" cy="12" r="9"/><path d="M12 16v-5M12 8h.01"/></svg>;
export const SendIcon = (props) => <svg viewBox="0 0 24 24" fill="currentColor" {...props}><path d="M3 20l18-8L3 4v6l12 2-12 2z"/></svg>;
export const TrashIcon = (props) => <svg {...base} {...props}><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13"/></svg>;
export const MicIcon = (props) => <svg {...base} {...props}><path d="M12 15a3 3 0 003-3V6a3 3 0 10-6 0v6a3 3 0 003 3z"/><path d="M19 11a7 7 0 01-14 0M12 18v3"/></svg>;
export const MoreIcon = (props) => <svg viewBox="0 0 24 24" fill="currentColor" {...props}><circle cx="12" cy="5" r="2"/><circle cx="12" cy="12" r="2"/><circle cx="12" cy="19" r="2"/></svg>;
export const CheckIcon = (props) => <svg {...base} {...props}><path d="M4 12l5 5L20 6"/></svg>;
// Two overlapping checks -- WhatsApp/Telegram's "delivered"/"read" tick.
export const DoubleCheckIcon = (props) => <svg {...base} {...props}><path d="M1 12l4 4L14 7"/><path d="M8 12l4 4L21 7"/></svg>;
export const MailIcon = (props) => <svg {...base} {...props}><rect x="3" y="5" width="18" height="14" rx="2"/><path d="M3 7l9 6 9-6"/></svg>;
export const CloseIcon = (props) => <svg {...base} {...props}><path d="M6 6l12 12M18 6L6 18"/></svg>;
export const PhoneIcon = (props) => <svg {...base} {...props}><path d="M22 16.92v3a2 2 0 0 1-2.18 2 19.79 19.79 0 0 1-8.63-3.07 19.5 19.5 0 0 1-6-6 19.79 19.79 0 0 1-3.07-8.67A2 2 0 0 1 4.11 2h3a2 2 0 0 1 2 1.72c.13.96.36 1.9.7 2.81a2 2 0 0 1-.45 2.11L8.09 9.91a16 16 0 0 0 6 6l1.27-1.27a2 2 0 0 1 2.11-.45c.91.34 1.85.57 2.81.7A2 2 0 0 1 22 16.92z"/></svg>;
export const AttachIcon = (props) => <svg {...base} {...props}><path d="M21.44 11.05l-9.19 9.19a5.5 5.5 0 01-7.78-7.78l9.19-9.19a3.5 3.5 0 014.95 4.95l-9.2 9.19a1.5 1.5 0 01-2.12-2.12l8.49-8.48"/></svg>;
// Cognitive Sharing's "find common ground" action -- a simple brain glyph,
// matching the Icons.psychology_outlined used for the same action on the
// Flutter client.
export const BrainIcon = (props) => <svg {...base} {...props}><path d="M9 4a3 3 0 00-3 3v.34A3.5 3.5 0 004 10.5 3.5 3.5 0 006 13.68V16a4 4 0 004 4M9 4a3 3 0 013 3v13M9 4c0-1.1.9-2 2-2M15 4a3 3 0 013 3v.34A3.5 3.5 0 0120 10.5 3.5 3.5 0 0118 13.68V16a4 4 0 01-4 4M15 4a3 3 0 00-3 3M15 4c0-1.1-.9-2-2-2"/></svg>;
// Cognitive Commerce's "order via Swiggy" action card -- a simple
// fork-and-plate glyph, no brand mark (no Swiggy trademark usage).
export const FoodOrderIcon = (props) => <svg {...base} {...props}><path d="M6 3v7a2 2 0 002 2h0a2 2 0 002-2V3M7 3v6M9 3v6M15 3c-1.5 0-3 1.5-3 4s1.5 4 3 4M6 12v9M15 12v9"/></svg>;
export const SunIcon = (props) => <svg {...base} {...props}><circle cx="12" cy="12" r="4.5"/><path d="M12 2.5v2.5M12 19v2.5M4.6 4.6l1.8 1.8M17.6 17.6l1.8 1.8M2.5 12h2.5M19 12h2.5M4.6 19.4l1.8-1.8M17.6 6.4l1.8-1.8"/></svg>;
// The Settings avatar's "change photo" badge -- matches Icons.camera_alt
// used for the same action on the Flutter client.
export const CameraIcon = (props) => <svg {...base} {...props}><path d="M4 8h3l1.5-2h7L17 8h3a1 1 0 011 1v10a1 1 0 01-1 1H4a1 1 0 01-1-1V9a1 1 0 011-1z"/><circle cx="12" cy="13" r="3.5"/></svg>;
export const CopyIcon = (props) => <svg {...base} {...props}><rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15H4a1 1 0 01-1-1V4a1 1 0 011-1h10a1 1 0 011 1v1"/></svg>;
export const ClockIcon = (props) => <svg {...base} {...props}><circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/></svg>;
export const AlertIcon = (props) => <svg {...base} {...props}><path d="M12 3l10 18H2L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>;
export const MoonIcon = (props) => <svg {...base} {...props}><path d="M20 14.5A8.5 8.5 0 019.5 4a8.5 8.5 0 1010.5 10.5z"/></svg>;
export const FileIcon = (props) => <svg {...base} {...props}><path d="M6 2h9l5 5v13a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"/><path d="M15 2v5h5"/></svg>;
export const DownloadIcon = (props) => <svg {...base} {...props}><path d="M12 3v12m0 0l-4-4m4 4l4-4"/><path d="M4 17v2a2 2 0 002 2h12a2 2 0 002-2v-2"/></svg>;
export const PlayIcon = (props) => <svg viewBox="0 0 24 24" fill="currentColor" {...props}><path d="M8 5v14l11-7z"/></svg>;
// Vehicle-policy confirm card -- matches Icons.shield_outlined used for the
// same card on the Flutter client.
export const ShieldIcon = (props) => <svg {...base} {...props}><path d="M12 3l8 3v6c0 4.5-3 8-8 9-5-1-8-4.5-8-9V6l8-3z"/></svg>;
// Expense confirm card -- matches Icons.payments_outlined used for the same
// card on the Flutter client.
export const ReceiptIcon = (props) => <svg {...base} {...props}><path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3z"/><path d="M9 8h6M9 12h6"/></svg>;
// Email draft card -- matches Icons.drafts_outlined used for the same card
// on the Flutter client.
export const DraftIcon = (props) => <svg {...base} {...props}><path d="M4 4h16v16H4z"/><path d="M4 4l8 7 8-7"/></svg>;
// Attach-menu "Photo" tile -- matches Icons.photo_outlined on the Flutter client.
export const PhotoIcon = (props) => <svg {...base} {...props}><rect x="3" y="4" width="18" height="16" rx="2"/><circle cx="8.5" cy="10" r="1.6"/><path d="M21 16l-5.5-5.5a1.5 1.5 0 00-2.1 0L5 19"/></svg>;
// Attach-menu "PDF document" tile -- matches Icons.picture_as_pdf_outlined.
export const PdfIcon = (props) => <svg {...base} {...props}><path d="M6 2h9l5 5v13a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"/><path d="M15 2v5h5"/><path d="M7.5 17v-4h1.3a1.2 1.2 0 010 2.4H7.5M11 13v4h1a1.6 1.6 0 000-4h-1zM14.5 17v-4H16.5M14.5 15H16"/></svg>;
// Phone-only top app bar's hamburger -> AppDrawer, matching Flutter's
// Icons.menu leading button on every screen's AppBar.
export const MenuIcon = (props) => <svg {...base} {...props}><path d="M4 7h16M4 12h16M4 17h16"/></svg>;
// Home screen components (see HomeSection.jsx) -- match the Flutter Home
// screen's own Material icon set one-for-one.
export const CloudIcon = (props) => <svg {...base} {...props}><path d="M7 18h10a4 4 0 00.5-7.97A5.5 5.5 0 007.14 9.1 4 4 0 007 18z"/></svg>;
export const BoltIcon = (props) => <svg viewBox="0 0 24 24" fill="currentColor" {...props}><path d="M13 2L4 14h6l-1 8 9-12h-6l1-8z"/></svg>;
export const ChevronRightIcon = (props) => <svg {...base} {...props}><path d="M9 5l7 7-7 7"/></svg>;
export const BellIcon = (props) => <svg {...base} {...props}><path d="M6 10a6 6 0 1112 0c0 3.2 1 5 2 6H4c1-1 2-2.8 2-6z"/><path d="M10 19a2 2 0 004 0"/></svg>;
export const SparkleIcon = (props) => <svg {...base} {...props}><path d="M12 3l1.8 4.9L19 9.5l-5.2 1.6L12 16l-1.8-4.9L5 9.5l5.2-1.6L12 3z"/></svg>;
export const PlusIcon = (props) => <svg {...base} {...props}><path d="M12 5v14M5 12h14"/></svg>;
