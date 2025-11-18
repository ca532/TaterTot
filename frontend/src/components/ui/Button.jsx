function Button({ 
  children, 
  onClick, 
  variant = 'primary', 
  size = 'md',
  icon: Icon,
  className = '' 
}) {
  const variants = {
    primary: 'bg-yellow-600 text-black hover:bg-yellow-700',
    secondary: 'bg-gray-200 text-gray-900 hover:bg-gray-300',
    outline: 'bg-transparent border-2 border-yellow-600 text-yellow-600 hover:bg-yellow-50',
  };

  const sizes = {
    sm: 'px-4 py-2 text-sm',
    md: 'px-6 py-3 text-base',
    lg: 'px-8 py-4 text-lg',
  };

  return (
    <button
      onClick={onClick}
      className={`
        inline-flex items-center gap-2 font-semibold rounded-lg
        transition-all duration-200 shadow-md hover:shadow-lg
        ${variants[variant]}
        ${sizes[size]}
        ${className}
      `}
    >
      {Icon && <Icon className="w-5 h-5" />}
      {children}
    </button>
  );
}

export default Button;