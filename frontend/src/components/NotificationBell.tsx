import { useEffect, useRef, useState } from 'react';
import { getNotifications, markNotificationRead } from '../api';
import type { TenderNotification } from '../types';
import { useNavigate } from '../router';
import { formatDate, formatTime } from '../utils';

// CR-008 W1: "forwarding should show up inside the app" — a badge on the
// profile/header area with an unread count, and a message-board-style
// dropdown ("X forwarded tender Y to you with the message ... in status W").
// Polls like Layout.tsx's own stats/health effect; ReviewQueue.tsx's presence
// heartbeat is the precedent for a periodic background poll on top of that.
const POLL_MS = 30000;

function kindVerb(kind: TenderNotification['kind']): string {
  return kind === 'forward' ? 'forwarded a tender to you' : 'asked you to review a tender';
}

function statusLabel(status: string): string {
  if (status === 'needs_review') return 'needs further review';
  if (status === 'shortlisted') return 'shortlisted';
  if (status === 'dismissed' || status === 'dismissed_final') return 'dismissed';
  if (status === 'reviewed') return 'reviewed';
  return 'new';
}

export default function NotificationBell() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const [notifications, setNotifications] = useState<TenderNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const containerRef = useRef<HTMLDivElement | null>(null);

  function load() {
    getNotifications()
      .then(r => { setNotifications(r.notifications); setUnreadCount(r.unread_count); })
      .catch(() => {});
  }

  useEffect(() => {
    load();
    const id = setInterval(load, POLL_MS);
    return () => clearInterval(id);
  }, []);

  useEffect(() => {
    if (!open) return;
    function onClickOutside(e: MouseEvent) {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClickOutside);
    return () => document.removeEventListener('mousedown', onClickOutside);
  }, [open]);

  function openNotification(n: TenderNotification) {
    setOpen(false);
    if (n.read_at === null) {
      markNotificationRead(n.id).then(load).catch(() => {});
    }
    navigate(`/scout/review-queue?pub=${encodeURIComponent(n.pub_number)}`);
  }

  // Opening the dropdown always fetches fresh, rather than showing whatever
  // the last POLL_MS tick happened to catch — a notification sent seconds
  // ago (e.g. by the viewer's own action, forwarding to themselves) would
  // otherwise be invisible until the next scheduled poll.
  function toggleOpen() {
    setOpen(o => {
      if (!o) load();
      return !o;
    });
  }

  return (
    <div ref={containerRef} style={{ position: 'relative' }}>
      <button
        onClick={toggleOpen}
        title="Notifications"
        style={{
          position: 'relative', width: 32, height: 32, borderRadius: '50%',
          background: '#151d2c', border: '1px solid #1a2334', color: '#8892a4',
          cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 15,
        }}
      >
        ✉
        {unreadCount > 0 && (
          <span style={{
            position: 'absolute', top: -3, right: -3, background: '#f87171', color: '#0f1623',
            fontSize: 10, fontWeight: 700, borderRadius: 9999, minWidth: 16, height: 16,
            display: 'flex', alignItems: 'center', justifyContent: 'center', padding: '0 3px',
            border: '2px solid #0d1320',
          }}>
            {unreadCount > 9 ? '9+' : unreadCount}
          </span>
        )}
      </button>

      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 40, width: 340, maxHeight: 420, overflowY: 'auto',
          background: '#151d2c', border: '1px solid #1a2334', borderRadius: 8,
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)', zIndex: 20,
        }}>
          <div style={{ padding: '10px 14px', borderBottom: '1px solid #1a2334', fontSize: 11, fontWeight: 700, letterSpacing: '0.08em', color: '#8892a4', textTransform: 'uppercase' }}>
            Notifications
          </div>
          {notifications.length === 0 ? (
            <div style={{ padding: 20, textAlign: 'center', color: '#8892a4', fontSize: 12 }}>
              Nothing yet.
            </div>
          ) : (
            notifications.map(n => (
              <div
                key={n.id}
                onClick={() => openNotification(n)}
                style={{
                  padding: '10px 14px', borderBottom: '1px solid #1a2334', cursor: 'pointer',
                  background: n.read_at === null ? 'rgba(96,165,250,0.05)' : 'transparent',
                }}
              >
                <div style={{ fontSize: 12, color: '#e2e8f0', display: 'flex', alignItems: 'flex-start', gap: 6 }}>
                  {n.read_at === null && (
                    <span style={{ width: 6, height: 6, borderRadius: '50%', background: '#60a5fa', marginTop: 5, flexShrink: 0 }} />
                  )}
                  <span>
                    <strong>{n.from_account_name}</strong> {kindVerb(n.kind)}{' '}
                    <span className="mono" style={{ color: '#8892a4' }}>({n.pub_number})</span>
                    {' '}— {statusLabel(n.status_at_send)}
                  </span>
                </div>
                {n.message && (
                  <div style={{ fontSize: 12, color: '#c8d0de', marginTop: 4, marginLeft: n.read_at === null ? 12 : 0 }}>
                    “{n.message}”
                  </div>
                )}
                <div style={{ fontSize: 11, color: '#4c5a70', marginTop: 4, marginLeft: n.read_at === null ? 12 : 0 }}>
                  {formatDate(n.created_at)} · {formatTime(n.created_at)}
                </div>
              </div>
            ))
          )}
        </div>
      )}
    </div>
  );
}
