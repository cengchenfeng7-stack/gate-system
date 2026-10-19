import { forwardRef, useState, useRef, Children, cloneElement } from 'react';
import { clsx } from 'clsx';
import { useClickOutside } from '@/hooks';

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: 'primary' | 'secondary' | 'danger' | 'ghost';
  size?: 'sm' | 'md' | 'lg';
  loading?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant = 'primary', size = 'md', loading, disabled, children, ...props }, ref) => {
    const base = 'btn';
    const variants = {
      primary: 'btn-primary',
      secondary: 'btn-secondary',
      danger: 'btn-danger',
      ghost: 'btn-ghost',
    };
    const sizes = {
      sm: 'btn-sm',
      md: '',
      lg: 'btn-lg',
    };
    
    return (
      <button
        ref={ref}
        className={clsx(base, variants[variant], sizes[size], className)}
        disabled={disabled || loading}
        {...props}
      >
        {loading && (
          <svg className="animate-spin-slow h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
            <circle cx="12" cy="12" r="10" strokeOpacity="0.3" />
            <path d="M12 2a10 10 0 0 1 10 10" strokeLinecap="round" />
          </svg>
        )}
        {children}
      </button>
    );
  }
);

Button.displayName = 'Button';

export interface InputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  error?: string;
  helperText?: string;
  size?: 'sm' | 'md';
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  ({ className, label, error, helperText, size = 'md', id, ...props }, ref) => {
    const inputId = id || `input_${Math.random().toString(36).slice(2, 8)}`;
    
    return (
      <div className="w-full">
        {label && (
          <label htmlFor={inputId} className="block text-sm font-medium text-temple-text mb-1.5">
            {label}
          </label>
        )}
        <input
          ref={ref}
          id={inputId}
          className={clsx('input', size === 'sm' && 'input-sm', error && 'border-temple-danger focus:border-temple-danger focus:ring-temple-danger/20', className)}
          aria-invalid={error ? 'true' : 'false'}
          aria-describedby={error ? `${inputId}-error` : helperText ? `${inputId}-helper` : undefined}
          {...props}
        />
        {error && (
          <p id={`${inputId}-error`} className="mt-1 text-sm text-temple-danger" role="alert">
            {error}
          </p>
        )}
        {helperText && !error && (
          <p id={`${inputId}-helper`} className="mt-1 text-sm text-temple-textMuted">
            {helperText}
          </p>
        )}
      </div>
    );
  }
);

Input.displayName = 'Input';

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string;
  error?: string;
  helperText?: string;
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  ({ className, label, error, helperText, id, ...props }, ref) => {
    const textareaId = id || `textarea_${Math.random().toString(36).slice(2, 8)}`;
    
    return (
      <div className="w-full">
        {label && (
          <label htmlFor={textareaId} className="block text-sm font-medium text-temple-text mb-1.5">
            {label}
          </label>
        )}
        <textarea
          ref={ref}
          id={textareaId}
          className={clsx('input min-h-[100px] resize-y', error && 'border-temple-danger focus:border-temple-danger focus:ring-temple-danger/20', className)}
          aria-invalid={error ? 'true' : 'false'}
          aria-describedby={error ? `${textareaId}-error` : helperText ? `${textareaId}-helper` : undefined}
          {...props}
        />
        {error && (
          <p id={`${textareaId}-error`} className="mt-1 text-sm text-temple-danger" role="alert">
            {error}
          </p>
        )}
        {helperText && !error && (
          <p id={`${textareaId}-helper`} className="mt-1 text-sm text-temple-textMuted">
            {helperText}
          </p>
        )}
      </div>
    );
  }
);

Textarea.displayName = 'Textarea';

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  error?: string;
  helperText?: string;
  options: Array<{ value: string; label: string }>;
  placeholder?: string;
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  ({ className, label, error, helperText, options, placeholder, id, ...props }, ref) => {
    const selectId = id || `select_${Math.random().toString(36).slice(2, 8)}`;
    
    return (
      <div className="w-full">
        {label && (
          <label htmlFor={selectId} className="block text-sm font-medium text-temple-text mb-1.5">
            {label}
          </label>
        )}
        <select
          ref={ref}
          id={selectId}
          className={clsx('input', error && 'border-temple-danger focus:border-temple-danger focus:ring-temple-danger/20', className)}
          aria-invalid={error ? 'true' : 'false'}
          aria-describedby={error ? `${selectId}-error` : helperText ? `${selectId}-helper` : undefined}
          {...props}
        >
          {placeholder && <option value="" disabled>{placeholder}</option>}
          {options.map((opt) => (
            <option key={opt.value} value={opt.value}>{opt.label}</option>
          ))}
        </select>
        {error && (
          <p id={`${selectId}-error`} className="mt-1 text-sm text-temple-danger" role="alert">
            {error}
          </p>
        )}
        {helperText && !error && (
          <p id={`${selectId}-helper`} className="mt-1 text-sm text-temple-textMuted">
            {helperText}
          </p>
        )}
      </div>
    );
  }
);

Select.displayName = 'Select';

export interface LabelProps extends React.LabelHTMLAttributes<HTMLLabelElement> {
  required?: boolean;
}

export const Label = forwardRef<HTMLLabelElement, LabelProps>(
  ({ className, required, children, ...props }, ref) => {
    return (
      <label
        ref={ref}
        className={clsx('block text-sm font-medium text-temple-text mb-1.5', className)}
        {...props}
      >
        {children}
        {required && <span className="text-temple-danger ml-1" aria-hidden="true">*</span>}
      </label>
    );
  }
);

Label.displayName = 'Label';

export interface CheckboxProps extends React.InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  description?: string;
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  ({ className, label, description, id, ...props }, ref) => {
    const checkboxId = id || `checkbox_${Math.random().toString(36).slice(2, 8)}`;
    
    return (
      <div className="flex items-start gap-3">
        <input
          ref={ref}
          id={checkboxId}
          type="checkbox"
          className={clsx('h-4 w-4 rounded border-temple-border bg-temple-surface text-temple-primary focus:ring-2 focus:ring-temple-primary/20 focus:ring-offset-2 focus:ring-offset-temple-bg', className)}
          {...props}
        />
        {(label || description) && (
          <div className="flex-1 min-w-0">
            {label && (
              <label htmlFor={checkboxId} className="block text-sm font-medium text-temple-text cursor-pointer">
                {label}
              </label>
            )}
            {description && (
              <p className="mt-0.5 text-sm text-temple-textMuted">{description}</p>
            )}
          </div>
        )}
      </div>
    );
  }
);

Checkbox.displayName = 'Checkbox';

export interface BadgeProps extends React.HTMLAttributes<HTMLSpanElement> {
  variant?: 'primary' | 'success' | 'warning' | 'danger' | 'neutral';
  size?: 'sm' | 'md';
}

export const Badge = forwardRef<HTMLSpanElement, BadgeProps>(
  ({ className, variant = 'primary', size = 'md', children, ...props }, ref) => {
    return (
      <span
        ref={ref}
        className={clsx(
          'badge inline-flex items-center px-2 py-0.5 rounded-full font-medium',
          variant === 'primary' && 'badge-primary',
          variant === 'success' && 'badge-success',
          variant === 'warning' && 'badge-warning',
          variant === 'danger' && 'badge-danger',
          variant === 'neutral' && 'badge-neutral',
          size === 'sm' && 'px-1.5 py-0 text-xs',
          size === 'md' && 'px-2 py-0.5 text-xs',
          className
        )}
        {...props}
      >
        {children}
      </span>
    );
  }
);

Badge.displayName = 'Badge';

export interface AvatarProps extends React.HTMLAttributes<HTMLDivElement> {
  src?: string | null;
  alt?: string;
  fallback?: string;
  size?: 'sm' | 'md' | 'lg' | 'xl';
  shape?: 'circle' | 'square';
  color?: string;
}

export const Avatar = forwardRef<HTMLDivElement, AvatarProps>(
  ({ className, src, alt, fallback, size = 'md', shape = 'circle', color = '#6d5aff', ...props }, ref) => {
    const sizes = {
      sm: 'h-6 w-6 text-xs',
      md: 'h-8 w-8 text-sm',
      lg: 'h-10 w-10 text-base',
      xl: 'h-12 w-12 text-lg',
    };
    const shapes = {
      circle: 'rounded-full',
      square: 'rounded-lg',
    };
    
    return (
      <div
        ref={ref}
        className={clsx(
          'inline-flex items-center justify-center font-medium select-none',
          sizes[size],
          shapes[shape],
          'bg-temple-primary/20 text-temple-primary',
          className
        )}
        style={{ backgroundColor: color + '20', color }}
        {...props}
      >
        {src ? (
          <img src={src} alt={alt || fallback || 'Avatar'} className={clsx(sizes[size], shapes[shape])} />
        ) : (
          fallback || '?'
        )}
      </div>
    );
  }
);

Avatar.displayName = 'Avatar';

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  hover?: boolean;
  padding?: 'none' | 'sm' | 'md' | 'lg';
}

export const Card = forwardRef<HTMLDivElement, CardProps>(
  ({ className, hover = false, padding = 'md', children, ...props }, ref) => {
    const paddings = {
      none: '',
      sm: 'p-3',
      md: 'p-5',
      lg: 'p-6',
    };
    
    return (
      <div
        ref={ref}
        className={clsx(
          'card',
          hover && 'card-hover',
          paddings[padding],
          className
        )}
        {...props}
      >
        {children}
      </div>
    );
  }
);

Card.displayName = 'Card';

export interface ModalProps {
  open: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  size?: 'sm' | 'md' | 'lg' | 'xl' | 'full';
  showClose?: boolean;
}

export function Modal({ open, onClose, title, children, size = 'md', showClose = true }: ModalProps) {
  if (!open) return null;
  
  const sizes = {
    sm: 'max-w-sm',
    md: 'max-w-md',
    lg: 'max-w-lg',
    xl: 'max-w-xl',
    full: 'max-w-4xl',
  };
  
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4 animate-fade-in" role="dialog" aria-modal="true" aria-labelledby={title ? 'modal-title' : undefined}>
      <div className="absolute inset-0 bg-black/60 backdrop-blur-sm" onClick={onClose} />
      <div className={clsx('relative w-full bg-temple-surface border border-temple-border rounded-xl shadow-2xl animate-scale-in', sizes[size])}>
        {(title || showClose) && (
          <div className="flex items-center justify-between px-5 py-4 border-b border-temple-border">
            {title && <h2 id="modal-title" className="text-lg font-semibold text-temple-text">{title}</h2>}
            {showClose && (
              <button onClick={onClose} className="p-1 rounded-lg hover:bg-temple-border transition-colors" aria-label="关闭">
                <svg className="h-5 w-5 text-temple-textMuted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                  <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            )}
          </div>
        )}
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

export interface ToastProps {
  id: string;
  type: 'success' | 'error' | 'warning' | 'info';
  message: string;
  onClose: (id: string) => void;
  duration?: number;
}

export function Toast({ id, type, message, onClose, duration = 3000 }: ToastProps) {
  const icons = {
    success: <svg className="h-5 w-5 text-temple-success" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>,
    error: <svg className="h-5 w-5 text-temple-danger" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m-2 2l2 2m-2 2l-2 2m2 2l-2 2m-2 2l-2-2m0 0a15.017 15.017 0 01-5.425-8.293 3 3 0 012.783-2.996 3 3 0 113.342 4.218A15.044 15.044 0 0112 21c-4.029 0-7.768-1.34-10.68-3.868z" /></svg>,
    warning: <svg className="h-5 w-5 text-temple-warning" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" /></svg>,
    info: <svg className="h-5 w-5 text-temple-primary" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>,
  };
  
  const colors = {
    success: 'bg-temple-success/10 border-temple-success/30',
    error: 'bg-temple-danger/10 border-temple-danger/30',
    warning: 'bg-temple-warning/10 border-temple-warning/30',
    info: 'bg-temple-primary/10 border-temple-primary/30',
  };
  
  return (
    <div className={clsx('fixed bottom-4 right-4 z-50 flex items-center gap-3 px-4 py-3 rounded-lg border animate-slide-in', colors[type])} role="alert">
      {icons[type]}
      <p className="text-sm font-medium text-temple-text">{message}</p>
      <button onClick={() => onClose(id)} className="ml-2 p-1 rounded hover:bg-white/10 transition-colors" aria-label="关闭">
        <svg className="h-4 w-4 text-temple-textMuted" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
        </svg>
      </button>
    </div>
  );
}

export function Toaster({ toasts, onClose }: { toasts: Array<{ id: string; type: 'success' | 'error' | 'warning' | 'info'; message: string; duration?: number }>; onClose: (id: string) => void }) {
  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map((toast) => (
        <Toast key={toast.id} {...toast} onClose={onClose} duration={toast.duration} />
      ))}
    </div>
  );
}

export interface EmptyStateProps {
  icon?: React.ReactNode;
  title: string;
  description?: string;
  action?: React.ReactNode;
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center py-12 px-4 text-center">
      {icon && <div className="mb-4 text-temple-textMuted/50">{icon}</div>}
      <h3 className="text-lg font-medium text-temple-text">{title}</h3>
      {description && <p className="mt-1 text-sm text-temple-textMuted">{description}</p>}
      {action && <div className="mt-4">{action}</div>}
    </div>
  );
}

export interface TabsProps {
  tabs: Array<{ id: string; label: string; icon?: React.ReactNode }>;
  activeTab: string;
  onChange: (id: string) => void;
  className?: string;
}

export function Tabs({ tabs, activeTab, onChange, className }: TabsProps) {
  return (
    <div className={clsx('flex gap-1 bg-temple-surface rounded-lg p-1', className)} role="tablist">
      {tabs.map((tab) => (
        <button
          key={tab.id}
          role="tab"
          aria-selected={activeTab === tab.id}
          onClick={() => onChange(tab.id)}
          className={clsx(
            'tab px-3 py-2 rounded-md text-sm font-medium',
            activeTab === tab.id ? 'tab-active' : ''
          )}
        >
          {tab.icon && <span className="inline-flex items-center gap-1.5">{tab.icon}</span>}
          {tab.label}
        </button>
      ))}
    </div>
  );
}

export interface DropdownProps {
  trigger: React.ReactNode;
  items: Array<{ label: string; onClick: () => void; icon?: React.ReactNode; danger?: boolean; disabled?: boolean }>;
  align?: 'left' | 'right';
}

export function Dropdown({ trigger, items, align = 'right' }: DropdownProps) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  
  useClickOutside(ref, () => setOpen(false));
  
  return (
    <div ref={ref} className="relative inline-block">
      <div onClick={() => setOpen(!open)} className="inline-block">{trigger}</div>
      {open && (
        <div className={clsx('absolute z-50 mt-1 min-w-[160px] bg-temple-surface border border-temple-border rounded-lg shadow-lg py-1 animate-scale-in', align === 'right' ? 'right-0' : 'left-0')}>
          {items.map((item, index) => (
            <button
              key={index}
              onClick={() => { item.onClick(); setOpen(false); }}
              disabled={item.disabled}
              className={clsx('w-full px-3 py-2 text-left text-sm flex items-center gap-2 transition-colors', item.danger ? 'text-temple-danger' : 'text-temple-text', item.disabled && 'opacity-50 cursor-not-allowed')}
            >
              {item.icon && <span className="h-4 w-4">{item.icon}</span>}
              {item.label}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export interface TooltipProps {
  content: string;
  children: React.ReactElement;
  position?: 'top' | 'bottom' | 'left' | 'right';
}

export function Tooltip({ content, children, position = 'top' }: TooltipProps) {
  const [visible, setVisible] = useState(false);
  const child = Children.only(children);
  
  return (
    <div className="relative inline-block" onMouseEnter={() => setVisible(true)} onMouseLeave={() => setVisible(false)}>
      {cloneElement(child, { onMouseEnter: () => setVisible(true), onMouseLeave: () => setVisible(false) })}
      {visible && (
        <div className={clsx('absolute z-50 px-2 py-1 text-xs text-temple-text bg-temple-bg border border-temple-border rounded shadow-lg whitespace-nowrap animate-fade-in', {
          'bottom-full left-1/2 -translate-x-1/2 mb-2': position === 'top',
          'top-full left-1/2 -translate-x-1/2 mt-2': position === 'bottom',
          'left-full top-1/2 -translate-y-1/2 ml-2': position === 'right',
          'right-full top-1/2 -translate-y-1/2 mr-2': position === 'left',
        })}>
          {content}
        </div>
      )}
    </div>
  );
}