function Badge({ children, variant = 'default' }) {
  const variants = {
    default: 'bg-gray-100 text-gray-800',
    success: 'bg-green-50 text-green-800 border-2 border-green-500',
    warning: 'bg-yellow-50 text-yellow-800 border-2 border-yellow-600',
    info: 'bg-blue-50 text-blue-800 border-2 border-blue-500',
  };

  return (
    <span className={`inline-flex items-center px-3 py-1 rounded-lg text-sm font-semibold ${variants[variant]}`}>
      {children}
    </span>
  );
}

export default Badge;