function ProgressBar({ progress, showLabel = true }) {
  return (
    <div className="w-full">
      <div className="w-full bg-gray-200 rounded-full h-2 mb-1">
        <div 
          className="bg-yellow-600 h-2 rounded-full transition-all duration-300 ease-out"
          style={{ width: `${progress}%` }}
        />
      </div>
      {showLabel && (
        <p className="text-xs text-gray-500 font-semibold text-center">
          {progress}% complete
        </p>
      )}
    </div>
  );
}

export default ProgressBar;