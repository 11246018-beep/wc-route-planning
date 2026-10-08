part of '../main.dart';

class AILoadingOverlay extends StatefulWidget {
  const AILoadingOverlay({super.key});

  @override
  State<AILoadingOverlay> createState() => _AILoadingOverlayState();
}

class _AILoadingOverlayState extends State<AILoadingOverlay>
    with SingleTickerProviderStateMixin {
  late AnimationController _controller;

  @override
  void initState() {
    super.initState();
    _controller = AnimationController(
      vsync: this,
      duration: const Duration(milliseconds: 1400),
    )..repeat(reverse: true);
  }

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    return Positioned.fill(
      child: Container(
        color: Colors.black.withValues(alpha: 0.45),
        child: Center(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              AnimatedBuilder(
                animation: _controller,
                builder: (context, child) {
                  final glow = 12 + (_controller.value * 24);

                  return Container(
                    width: 110,
                    height: 110,
                    decoration: BoxDecoration(
                      shape: BoxShape.circle,
                      color: Colors.white,
                      boxShadow: [
                        BoxShadow(
                          color: Colors.cyanAccent.withValues(alpha: 0.75),
                          blurRadius: glow,
                          spreadRadius: 2,
                        ),
                        BoxShadow(
                          color: Colors.blueAccent.withValues(alpha: 0.35),
                          blurRadius: glow + 12,
                          spreadRadius: 6,
                        ),
                      ],
                    ),
                    child: const Icon(
                      Icons.auto_awesome,
                      size: 48,
                      color: Colors.indigo,
                    ),
                  );
                },
              ),
              const SizedBox(height: 24),
              const Text(
                'AI 辨識中...',
                style: TextStyle(
                  fontSize: 24,
                  fontWeight: FontWeight.bold,
                  color: Colors.white,
                  letterSpacing: 1.2,
                ),
              ),
              const SizedBox(height: 10),
              const Text(
                '請稍候，正在分析照片與回傳結果',
                style: TextStyle(fontSize: 15, color: Colors.white70),
              ),
              const SizedBox(height: 18),
              const SizedBox(
                width: 30,
                height: 30,
                child: CircularProgressIndicator(
                  strokeWidth: 3,
                  color: Colors.white,
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}
