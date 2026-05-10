import { useState } from 'react';
import { Lock, Eye, EyeOff, AlertCircle } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';

function LoginScreen() {
  const [password, setPassword] = useState('');
  const [showPassword, setShowPassword] = useState(false);
  const [error, setError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const { login } = useAuth();

  const handleSubmit = async () => {
    setError('');
    setIsLoading(true);

    const result = await login(password);
    
    if (!result.success) {
      setError(result.error || 'Incorrect password. Please try again.');
      setPassword('');
    }
    
    setIsLoading(false);
  };

  const handleKeyPress = (e) => {
    if (e.key === 'Enter' && password) {
      handleSubmit();
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-[#faf8f3] to-white flex items-center justify-center p-4">
      <div className="w-full max-w-md">
        <div className="text-center mb-8">
          <div className="inline-flex items-center justify-center w-20 h-20 rounded-full bg-white border-4 border-[#b8860b] mb-4 shadow-lg">
            <Lock className="w-10 h-10 text-[#b8860b]" />
          </div>
          <h1 className="text-4xl font-bold text-[#b8860b] mb-2">Claire Adler</h1>
          <p className="text-gray-600 font-medium">Luxury PR</p>
        </div>

        <div className="bg-white rounded-lg shadow-2xl border-2 border-[#b8860b] p-8">
          <h2 className="text-2xl font-bold text-gray-900 mb-2 text-center">
            Welcome Back
          </h2>
          <p className="text-gray-600 text-center mb-6">
            Please enter your password to continue
          </p>

          <div className="space-y-4">
            <div>
              <label htmlFor="password" className="block text-sm font-semibold text-gray-700 mb-2">
                Password
              </label>
              <div className="relative">
                <input
                  id="password"
                  type={showPassword ? 'text' : 'password'}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  onKeyPress={handleKeyPress}
                  className="w-full px-4 py-3 border-2 border-gray-300 rounded-lg focus:border-[#b8860b] focus:outline-none transition-colors"
                  placeholder="Enter your password"
                  autoFocus
                />
                <button
                  onClick={() => setShowPassword(!showPassword)}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 hover:text-gray-700"
                >
                  {showPassword ? <EyeOff className="w-5 h-5" /> : <Eye className="w-5 h-5" />}
                </button>
              </div>
            </div>

            {error && (
              <div className="flex items-center gap-2 p-3 bg-red-50 border-2 border-red-500 rounded-lg">
                <AlertCircle className="w-5 h-5 text-red-600 flex-shrink-0" />
                <p className="text-sm text-red-800 font-semibold">{error}</p>
              </div>
            )}

            <button
              onClick={handleSubmit}
              disabled={isLoading || !password}
              className="w-full py-3 bg-[#b8860b] text-black font-bold rounded-lg hover:bg-[#8b6914] disabled:bg-gray-300 disabled:cursor-not-allowed transition-colors shadow-md hover:shadow-lg"
            >
              {isLoading ? (
                <span className="flex items-center justify-center gap-2">
                  <div className="w-5 h-5 border-2 border-black border-t-transparent rounded-full animate-spin"></div>
                  Signing in...
                </span>
              ) : (
                'Sign In'
              )}
            </button>
          </div>

          <div className="mt-6 p-4 bg-[#faf8f3] rounded-lg border border-[#b8860b]">
            <p className="text-xs text-gray-600 text-center">
              🔒 This dashboard is password protected. Contact your administrator if you need access.
            </p>
          </div>
        </div>
      </div>
    </div>
  );
}

export default LoginScreen;
