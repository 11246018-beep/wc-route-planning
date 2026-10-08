import 'dart:async';
import 'dart:convert';
import 'dart:ui';
import 'package:flutter/material.dart';
import 'package:geolocator/geolocator.dart';
import 'package:http/http.dart' as http;
import 'package:google_maps_flutter/google_maps_flutter.dart';
import 'package:url_launcher/url_launcher.dart';
import 'dart:io';
import 'package:image_picker/image_picker.dart';
import 'package:shared_preferences/shared_preferences.dart';

part 'shared/app_state.dart';
part 'app.dart';
part 'services/api_service.dart';
part 'services/background_location_tracker.dart';
part 'pages/login_page.dart';
part 'pages/main_map_screen.dart';
part 'pages/driver_profile_page.dart';
part 'pages/schedule_page.dart';
part 'pages/live_location_page.dart';
part 'widgets/ai_loading_overlay.dart';
part 'pages/report_page.dart';

Future<void> main() async {
  WidgetsFlutterBinding.ensureInitialized();
  final prefs = await SharedPreferences.getInstance();
  kDriverToken = prefs.getString('driver_auth_token');
  kCompanyKey = prefs.getString('company_key') ?? kCompanyKey;

  runApp(const DriverApp());
}
