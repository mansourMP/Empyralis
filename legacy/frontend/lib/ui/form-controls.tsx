'use client';

import type { HTMLAttributes, KeyboardEvent, PropsWithChildren, ReactNode } from 'react';
import { useState } from 'react';

import { AppButton, AppInput, AppSelect, AppTextarea, joinClassNames } from '@/lib/ui/primitives';

function resolveFormGridVariant(columns: string | undefined): string {
  if (columns === '1fr') {
    return 'app-form-grid--one';
  }
  if (columns === 'repeat(2, minmax(0, 1fr))') {
    return 'app-form-grid--two';
  }
  if (columns === 'repeat(auto-fit, minmax(12rem, 1fr))') {
    return 'app-form-grid--auto-12';
  }
  if (columns === 'repeat(auto-fit, minmax(14rem, 1fr))') {
    return 'app-form-grid--auto-14';
  }
  return 'app-form-grid--auto';
}

export function FormSection({
  title,
  description,
  className,
  children,
}: PropsWithChildren<{
  title: ReactNode;
  description?: ReactNode;
  className?: string;
}>) {
  return (
    <section className={joinClassNames('app-form-section', className)}>
      <div className="app-form-section__header">
        <strong className="app-form-section__title">{title}</strong>
        {description ? (
          <span className="app-form-section__description">{description}</span>
        ) : null}
      </div>
      {children}
    </section>
  );
}

export function FormGrid({
  children,
  columns = 'repeat(auto-fit, minmax(14rem, 1fr))',
}: PropsWithChildren<{
  columns?: string;
}>) {
  return (
    <div className={joinClassNames('app-form-grid', resolveFormGridVariant(columns))}>
      {children}
    </div>
  );
}

export function FormField({
  label,
  hint,
  children,
  className,
  ...props
}: PropsWithChildren<HTMLAttributes<HTMLLabelElement> & {
  label: ReactNode;
  hint?: ReactNode;
}>) {
  return (
    <label {...props} className={joinClassNames('app-form-field', className)}>
      <span className="app-form-field__label">{label}</span>
      {children}
      {hint ? (
        <span className="app-form-field__hint">{hint}</span>
      ) : null}
    </label>
  );
}

export const FormInput = AppInput;
export const FormTextarea = AppTextarea;
export const FormSelect = AppSelect;

export function FormTokenListEditor({
  value,
  onChange,
  placeholder = 'Add item',
  addLabel = 'Add',
  emptyLabel = 'No items added.',
}: {
  value: string[];
  onChange: (nextValue: string[]) => void;
  placeholder?: string;
  addLabel?: string;
  emptyLabel?: string;
}) {
  const [draft, setDraft] = useState('');

  function commitDraft() {
    const token = draft.trim();
    if (!token) {
      return;
    }
    if (!value.includes(token)) {
      onChange([...value, token]);
    }
    setDraft('');
  }

  function handleKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === 'Enter' || event.key === ',') {
      event.preventDefault();
      commitDraft();
    }
  }

  return (
    <div className="app-token-editor">
      {value.length > 0 ? (
        <div className="app-token-editor__list">
          {value.map((item) => (
            <span key={item} className="app-token-editor__item">
              {item}
              <button
                type="button"
                className="app-token-editor__item-remove"
                aria-label={`Remove ${item}`}
                onClick={() => {
                  onChange(value.filter((entry) => entry !== item));
                }}
              >
                ×
              </button>
            </span>
          ))}
        </div>
      ) : (
        <div className="app-token-editor__empty">{emptyLabel}</div>
      )}

      <div className="app-token-editor__composer">
        <AppInput
          value={draft}
          placeholder={placeholder}
          onChange={(event) => {
            setDraft(event.currentTarget.value);
          }}
          onKeyDown={handleKeyDown}
        />
        <AppButton
          type="button"
          tone="secondary"
          className="app-button--compact"
          onClick={commitDraft}
        >
          {addLabel}
        </AppButton>
      </div>
    </div>
  );
}

export function FormReadout({
  label,
  value,
}: {
  label: ReactNode;
  value: ReactNode;
}) {
  return (
    <div className="app-form-readout">
      <span className="app-form-readout__label">{label}</span>
      <span className="app-form-readout__value">{value}</span>
    </div>
  );
}
