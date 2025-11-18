function Card({ children, className = '', hover = false, border = 'gray', padding = 'p-6' }) {
  const borderColors = {
    gray: 'border-gray-200 hover:border-yellow-600',
    yellow: 'border-yellow-600',
    green: 'border-green-500',
  };

  return (
    <div 
      className={`
        bg-white rounded-lg shadow-lg border-2 
        ${borderColors[border]}
        ${hover ? 'transition-all hover:shadow-xl' : ''}
        ${padding}
        ${className}
      `}
    >
      {children}
    </div>
  );
}

export default Card;