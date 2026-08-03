// Backdrop-dismiss for modals, done right.
//
// A modal overlay should close when you click the dark backdrop — but NOT when a
// drag that began inside the dialog (e.g. selecting input text) happens to release
// on the backdrop. The browser fires that `click` on the overlay (the common
// ancestor of press+release), so a naive `onClick={close}` closes the modal mid-
// selection.
//
// Fix: only close when the press STARTED on the backdrop itself. We record that on
// mousedown, stored on the overlay DOM node's dataset so no React state/ref is
// needed and it works for any number of modals.
//
// Usage:  <div className="modal-overlay" {...backdrop(() => setOpen(false))}>
export function backdrop(onClose) {
  return {
    onMouseDown: (e) => {
      e.currentTarget.dataset.downSelf = e.target === e.currentTarget ? '1' : '0'
    },
    onClick: (e) => {
      if (e.target === e.currentTarget && e.currentTarget.dataset.downSelf === '1') onClose()
    },
  }
}
