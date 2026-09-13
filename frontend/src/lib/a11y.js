// Every list row in the app (Chats/Tasks/Profiles/Friends) is a clickable
// <div>, not a real <button> -- can't be a button since rows nest their own
// interactive children (message/call icon buttons, a checkbox toggle), and
// a <button> can't contain another <button> per HTML. role="button" +
// tabIndex + this keydown handler makes the div itself keyboard-operable
// (Enter/Space activate it) without changing that structure.
export function onEnterOrSpace(handler) {
  return (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      handler(e);
    }
  };
}
