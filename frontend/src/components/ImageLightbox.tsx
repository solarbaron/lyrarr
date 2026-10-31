import { useEffect, useRef, useState } from 'react';
import { FocusTrap, Loader, Portal } from '@mantine/core';
import { faCompactDisc, faXmark } from '@fortawesome/free-solid-svg-icons';
import { FontAwesomeIcon } from '@fortawesome/react-fontawesome';
import { IconButton } from './ui';
import c from './ImageLightbox.module.css';

interface ImageLightboxProps {
  src: string;
  alt?: string;
  onClose: () => void;
}

/**
 * Full-size image viewer (DESIGN.md 8.18): portal overlay, Esc or a backdrop
 * click closes, focus is trapped inside and returned to the trigger on close.
 */
export default function ImageLightbox({ src, alt, onClose }: ImageLightboxProps) {
  const label = alt || 'Album cover';
  const [dims, setDims] = useState<{ w: number; h: number } | null>(null);
  const [failed, setFailed] = useState(false);
  const closeRef = useRef(onClose);
  closeRef.current = onClose;

  // Close on Escape; stopPropagation so page-level Esc handlers (bulk bar,
  // table cursor, a modal underneath) don't also react.
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key !== 'Escape') return;
      e.preventDefault();
      e.stopPropagation();
      closeRef.current();
    };
    document.addEventListener('keydown', handleKey, true);
    return () => document.removeEventListener('keydown', handleKey, true);
  }, []);

  // Return focus to whatever opened the lightbox; lock page scroll meanwhile.
  useEffect(() => {
    const trigger = document.activeElement as HTMLElement | null;
    const { overflow } = document.body.style;
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = overflow;
      if (trigger && document.contains(trigger)) trigger.focus({ preventScroll: true });
    };
  }, []);

  return (
    <Portal>
      <FocusTrap active>
        <div className={c.backdrop} onClick={onClose} role="dialog" aria-modal="true" aria-label={label}>
          {/* A click or tap on the image keeps focus where it is (the Close button)
              instead of dropping it to <body> while the lightbox stays open. */}
          <div className={c.content} onClick={(e) => e.stopPropagation()} onMouseDown={(e) => e.preventDefault()}>
            {failed ? (
              <div className={c.fallback} role="img" aria-label={label}>
                <FontAwesomeIcon icon={faCompactDisc} />
                <span>Couldn't load this image</span>
              </div>
            ) : (
              <>
                {!dims && <div className={c.loading} aria-hidden="true"><Loader /></div>}
                <img
                  src={src}
                  alt={label}
                  className={`${c.image} ly-sleeve`}
                  onLoad={(e) => setDims({ w: e.currentTarget.naturalWidth, h: e.currentTarget.naturalHeight })}
                  onError={() => setFailed(true)}
                  data-loaded={dims ? true : undefined}
                />
              </>
            )}
            <div className={c.caption}>
              <span className={c.captionText}>{label}</span>
              {dims && <span className={c.dims}>{dims.w}×{dims.h}</span>}
            </div>
          </div>
          <IconButton
            className={c.close}
            icon={faXmark}
            label="Close"
            shortcut="Esc"
            variant="default"
            size="lg"
            tooltipPosition="left"
            onClick={(e) => { e.stopPropagation(); onClose(); }}
            data-autofocus
          />
        </div>
      </FocusTrap>
    </Portal>
  );
}
