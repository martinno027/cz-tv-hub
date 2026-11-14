/*
 * Domů's header (spec/apps.md "Domů", T58), all offline: the full Czech date, the name day (or the state holiday),
 * and at most two calendar lines (a state holiday tomorrow, a clock change, school holidays, a full or new moon).
 * Days are Prague calendar days 'YYYY-MM-DD' (CT.Days). Golden cases: spec/fixtures/calendar_cases.json; the tables
 * below are test-checked equal to that file and to spec/fixtures/namedays_cz.json.
 */
(function (root) {
  'use strict';
  var CT = root.CT = root.CT || {};

  var DAY = 24 * 3600 * 1000;
  var WEEKDAYS = ['pondělí', 'úterý', 'středa', 'čtvrtek', 'pátek', 'sobota', 'neděle'];
  var MONTHS = ['ledna', 'února', 'března', 'dubna', 'května', 'června', 'července', 'srpna', 'září', 'října',
    'listopadu', 'prosince'];
  var MAX_LINES = 2;
  var AHEAD_DAYS = 6; // a clock change or school holidays this many days ahead are announced

  // zákon č. 245/2000 Sb.; Velký pátek and Velikonoční pondělí are days from Easter Sunday
  var STATE_HOLIDAYS = {
    '01-01': 'Den obnovy samostatného českého státu',
    '05-01': 'Svátek práce',
    '05-08': 'Den vítězství',
    '07-05': 'Den slovanských věrozvěstů Cyrila a Metoděje',
    '07-06': 'Den upálení mistra Jana Husa',
    '09-28': 'Den české státnosti',
    '10-28': 'Den vzniku samostatného československého státu',
    '11-17': 'Den boje za svobodu a demokracii a Mezinárodní den studentstva',
    '12-24': 'Štědrý den',
    '12-25': '1. svátek vánoční',
    '12-26': '2. svátek vánoční'
  };
  var EASTER_HOLIDAYS = { '-2': 'Velký pátek', '1': 'Velikonoční pondělí' };

  // MŠMT nationwide holidays of the school year (spring holidays depend on the district: not shown). Updated once a
  // year from spec/fixtures/calendar_cases.json; outside the table nothing is shown.
  var SCHOOL_HOLIDAYS = [
    { name: 'Podzimní prázdniny', from: '2026-10-29', to: '2026-10-30' },
    { name: 'Vánoční prázdniny', from: '2026-12-23', to: '2027-01-03' },
    { name: 'Pololetní prázdniny', from: '2027-01-29', to: '2027-01-29' },
    { name: 'Velikonoční prázdniny', from: '2027-03-25', to: '2027-03-25' },
    { name: 'Hlavní prázdniny', from: '2027-07-01', to: '2027-08-31' }
  ];

  // The Czech civil name-day calendar ('MM-DD' → names; null = a state holiday or no name), = namedays_cz.json.
  var NAMEDAYS = {
    '01-01': null, '01-02': ['Karina'], '01-03': ['Radmila'], '01-04': ['Diana'], '01-05': ['Dalimil'],
    '01-06': ['Kašpar', 'Melichar', 'Baltazar'], '01-07': ['Vilma'], '01-08': ['Čestmír'], '01-09': ['Vladan'],
    '01-10': ['Břetislav'], '01-11': ['Bohdana'], '01-12': ['Pravoslav'], '01-13': ['Edita'], '01-14': ['Radovan'],
    '01-15': ['Alice'], '01-16': ['Ctirad'], '01-17': ['Drahoslav'], '01-18': ['Vladislav'], '01-19': ['Doubravka'],
    '01-20': ['Ilona'], '01-21': ['Běla'], '01-22': ['Slavomír'], '01-23': ['Zdeněk'], '01-24': ['Milena'],
    '01-25': ['Miloš'], '01-26': ['Zora'], '01-27': ['Ingrid'], '01-28': ['Otýlie'], '01-29': ['Zdislava'],
    '01-30': ['Robin'], '01-31': ['Marika'], '02-01': ['Hynek'], '02-02': ['Nela'], '02-03': ['Blažej'],
    '02-04': ['Jarmila'], '02-05': ['Dobromila'], '02-06': ['Vanda'], '02-07': ['Veronika'], '02-08': ['Milada'],
    '02-09': ['Apolena'], '02-10': ['Mojmír'], '02-11': ['Božena'], '02-12': ['Slavěna'], '02-13': ['Věnceslav'],
    '02-14': ['Valentýn'], '02-15': ['Jiřina'], '02-16': ['Ljuba'], '02-17': ['Miloslava'], '02-18': ['Gizela'],
    '02-19': ['Patrik'], '02-20': ['Oldřich'], '02-21': ['Lenka'], '02-22': ['Petr'], '02-23': ['Svatopluk'],
    '02-24': ['Matěj'], '02-25': ['Liliana'], '02-26': ['Dorota'], '02-27': ['Alexandr'], '02-28': ['Lumír'],
    '02-29': ['Horymír'], '03-01': ['Bedřich'], '03-02': ['Anežka'], '03-03': ['Kamil'], '03-04': ['Stela'],
    '03-05': ['Kazimír'], '03-06': ['Miroslav'], '03-07': ['Tomáš'], '03-08': ['Gabriela'], '03-09': ['Františka'],
    '03-10': ['Viktorie'], '03-11': ['Anděla'], '03-12': ['Řehoř'], '03-13': ['Růžena'], '03-14': ['Rút', 'Matylda'],
    '03-15': ['Ida'], '03-16': ['Elena', 'Herbert'], '03-17': ['Vlastimil'], '03-18': ['Eduard'], '03-19': ['Josef'],
    '03-20': ['Světlana'], '03-21': ['Radek'], '03-22': ['Leona'], '03-23': ['Ivona'], '03-24': ['Gabriel'],
    '03-25': ['Marián'], '03-26': ['Emanuel'], '03-27': ['Dita'], '03-28': ['Soňa'], '03-29': ['Taťána'],
    '03-30': ['Arnošt'], '03-31': ['Kvido'], '04-01': ['Hugo'], '04-02': ['Erika'], '04-03': ['Richard'],
    '04-04': ['Ivana'], '04-05': ['Miroslava'], '04-06': ['Vendula'], '04-07': ['Heřman'], '04-08': ['Ema'],
    '04-09': ['Dušan'], '04-10': ['Darja'], '04-11': ['Izabela'], '04-12': ['Julius'], '04-13': ['Aleš'],
    '04-14': ['Vincenc'], '04-15': ['Anastázie'], '04-16': ['Irena'], '04-17': ['Rudolf'], '04-18': ['Valérie'],
    '04-19': ['Rostislav'], '04-20': ['Marcela'], '04-21': ['Alexandra'], '04-22': ['Evženie'], '04-23': ['Vojtěch'],
    '04-24': ['Jiří'], '04-25': ['Marek'], '04-26': ['Oto'], '04-27': ['Jaroslav'], '04-28': ['Vlastislav'],
    '04-29': ['Robert'], '04-30': ['Blahoslav'], '05-01': null, '05-02': ['Zikmund'], '05-03': ['Alexej'],
    '05-04': ['Květoslav'], '05-05': ['Klaudie'], '05-06': ['Radoslav'], '05-07': ['Stanislav'], '05-08': null,
    '05-09': ['Ctibor'], '05-10': ['Blažena'], '05-11': ['Svatava'], '05-12': ['Pankrác'], '05-13': ['Servác'],
    '05-14': ['Bonifác'], '05-15': ['Žofie'], '05-16': ['Přemysl'], '05-17': ['Aneta'], '05-18': ['Nataša'],
    '05-19': ['Ivo'], '05-20': ['Zbyšek'], '05-21': ['Monika'], '05-22': ['Emil'], '05-23': ['Vladimír'],
    '05-24': ['Jana'], '05-25': ['Viola'], '05-26': ['Filip'], '05-27': ['Valdemar'], '05-28': ['Vilém'],
    '05-29': ['Maxmilián'], '05-30': ['Ferdinand'], '05-31': ['Kamila'], '06-01': ['Laura'], '06-02': ['Jarmil'],
    '06-03': ['Tamara'], '06-04': ['Dalibor'], '06-05': ['Dobroslav'], '06-06': ['Norbert'], '06-07': ['Iveta'],
    '06-08': ['Medard'], '06-09': ['Stanislava'], '06-10': ['Gita'], '06-11': ['Bruno'], '06-12': ['Antonie'],
    '06-13': ['Antonín'], '06-14': ['Roland'], '06-15': ['Vít'], '06-16': ['Zbyněk'], '06-17': ['Adolf'],
    '06-18': ['Milan'], '06-19': ['Leoš'], '06-20': ['Květa'], '06-21': ['Alois'], '06-22': ['Pavla'],
    '06-23': ['Zdeňka'], '06-24': ['Jan'], '06-25': ['Ivan'], '06-26': ['Adriana'], '06-27': ['Ladislav'],
    '06-28': ['Lubomír'], '06-29': ['Petr', 'Pavel'], '06-30': ['Šárka'], '07-01': ['Jaroslava'],
    '07-02': ['Patricie'], '07-03': ['Radomír'], '07-04': ['Prokop'], '07-05': null, '07-06': null,
    '07-07': ['Bohuslava'], '07-08': ['Nora'], '07-09': ['Drahoslava'], '07-10': ['Libuše', 'Amálie'],
    '07-11': ['Olga'], '07-12': ['Bořek'], '07-13': ['Markéta'], '07-14': ['Karolína'], '07-15': ['Jindřich'],
    '07-16': ['Luboš'], '07-17': ['Martina'], '07-18': ['Drahomíra'], '07-19': ['Čeněk'], '07-20': ['Ilja'],
    '07-21': ['Vítězslav'], '07-22': ['Magdaléna'], '07-23': ['Libor'], '07-24': ['Kristýna'], '07-25': ['Jakub'],
    '07-26': ['Anna'], '07-27': ['Věroslav'], '07-28': ['Viktor'], '07-29': ['Marta'], '07-30': ['Bořivoj'],
    '07-31': ['Ignác'], '08-01': ['Oskar'], '08-02': ['Gustav'], '08-03': ['Miluše'], '08-04': ['Dominik'],
    '08-05': ['Kristián'], '08-06': ['Oldřiška'], '08-07': ['Lada'], '08-08': ['Soběslav'], '08-09': ['Roman'],
    '08-10': ['Vavřinec'], '08-11': ['Zuzana'], '08-12': ['Klára'], '08-13': ['Alena'], '08-14': ['Alan'],
    '08-15': ['Hana'], '08-16': ['Jáchym'], '08-17': ['Petra'], '08-18': ['Helena'], '08-19': ['Ludvík'],
    '08-20': ['Bernard'], '08-21': ['Johana'], '08-22': ['Bohuslav'], '08-23': ['Sandra'], '08-24': ['Bartoloměj'],
    '08-25': ['Radim'], '08-26': ['Luděk'], '08-27': ['Otakar'], '08-28': ['Augustýn'], '08-29': ['Evelína'],
    '08-30': ['Vladěna'], '08-31': ['Pavlína'], '09-01': ['Linda', 'Samuel'], '09-02': ['Adéla'],
    '09-03': ['Bronislav'], '09-04': ['Jindřiška'], '09-05': ['Boris'], '09-06': ['Boleslav'], '09-07': ['Regína'],
    '09-08': ['Mariana'], '09-09': ['Daniela'], '09-10': ['Irma'], '09-11': ['Denisa'], '09-12': ['Marie'],
    '09-13': ['Lubor'], '09-14': ['Radka'], '09-15': ['Jolana'], '09-16': ['Ludmila'], '09-17': ['Naděžda'],
    '09-18': ['Kryštof'], '09-19': ['Zita'], '09-20': ['Oleg'], '09-21': ['Matouš'], '09-22': ['Darina'],
    '09-23': ['Berta'], '09-24': ['Jaromír'], '09-25': ['Zlata'], '09-26': ['Andrea'], '09-27': ['Jonáš'],
    '09-28': ['Václav'], '09-29': ['Michal'], '09-30': ['Jeroným'], '10-01': ['Igor'], '10-02': ['Olívie', 'Oliver'],
    '10-03': ['Bohumil'], '10-04': ['František'], '10-05': ['Eliška'], '10-06': ['Hanuš'], '10-07': ['Justýna'],
    '10-08': ['Věra'], '10-09': ['Štefan', 'Sára'], '10-10': ['Marina'], '10-11': ['Andrej'], '10-12': ['Marcel'],
    '10-13': ['Renáta'], '10-14': ['Agáta'], '10-15': ['Tereza'], '10-16': ['Havel'], '10-17': ['Hedvika'],
    '10-18': ['Lukáš'], '10-19': ['Michala'], '10-20': ['Vendelín'], '10-21': ['Brigita'], '10-22': ['Sabina'],
    '10-23': ['Teodor'], '10-24': ['Nina'], '10-25': ['Beáta'], '10-26': ['Erik'], '10-27': ['Šarlota', 'Zoe'],
    '10-28': null, '10-29': ['Silvie'], '10-30': ['Tadeáš'], '10-31': ['Štěpánka'], '11-01': ['Felix'], '11-02': null,
    '11-03': ['Hubert'], '11-04': ['Karel'], '11-05': ['Miriam'], '11-06': ['Liběna'], '11-07': ['Saskie'],
    '11-08': ['Bohumír'], '11-09': ['Bohdan'], '11-10': ['Evžen'], '11-11': ['Martin'], '11-12': ['Benedikt'],
    '11-13': ['Tibor'], '11-14': ['Sáva'], '11-15': ['Leopold'], '11-16': ['Otmar'], '11-17': ['Mahulena'],
    '11-18': ['Romana'], '11-19': ['Alžběta'], '11-20': ['Nikola'], '11-21': ['Albert'], '11-22': ['Cecílie'],
    '11-23': ['Klement'], '11-24': ['Emílie'], '11-25': ['Kateřina'], '11-26': ['Artur'], '11-27': ['Xenie'],
    '11-28': ['René'], '11-29': ['Zina'], '11-30': ['Ondřej'], '12-01': ['Iva'], '12-02': ['Blanka'],
    '12-03': ['Svatoslav'], '12-04': ['Barbora'], '12-05': ['Jitka'], '12-06': ['Mikuláš'], '12-07': ['Ambrož'],
    '12-08': ['Květoslava'], '12-09': ['Vratislav'], '12-10': ['Julie'], '12-11': ['Dana'], '12-12': ['Simona'],
    '12-13': ['Lucie'], '12-14': ['Lýdie'], '12-15': ['Radana'], '12-16': ['Albína'], '12-17': ['Daniel'],
    '12-18': ['Miloslav'], '12-19': ['Ester'], '12-20': ['Dagmar'], '12-21': ['Natálie'], '12-22': ['Šimon'],
    '12-23': ['Vlasta'], '12-24': ['Adam', 'Eva'], '12-25': null, '12-26': ['Štěpán'], '12-27': ['Žaneta'],
    '12-28': ['Bohumila'], '12-29': ['Judita'], '12-30': ['David'], '12-31': ['Silvestr']
  };

  function pad2(n) {
    return n < 10 ? '0' + n : String(n);
  }

  /** 'YYYY-MM-DD' → epoch ms of its UTC midnight (a day number × DAY). */
  function parse(day) {
    var p = day.split('-');
    return Date.UTC(+p[0], +p[1] - 1, +p[2]);
  }

  function format(ms) {
    var d = new Date(ms);
    return d.getUTCFullYear() + '-' + pad2(d.getUTCMonth() + 1) + '-' + pad2(d.getUTCDate());
  }

  function between(a, b) {
    return Math.round((parse(b) - parse(a)) / DAY);
  }

  /** `4. listopadu` */
  function dayMonth(day) {
    var d = new Date(parse(day));
    return d.getUTCDate() + '. ' + MONTHS[d.getUTCMonth()];
  }

  function lowerFirst(text) {
    return text.charAt(0).toLowerCase() + text.slice(1);
  }

  /** Easter Sunday of `year` by the Gregorian computus (Meeus/Jones/Butcher), 'YYYY-MM-DD'. */
  function easter(year) {
    var a = year % 19;
    var b = Math.floor(year / 100);
    var c = year % 100;
    var d = Math.floor(b / 4);
    var e = b % 4;
    var f = Math.floor((b + 8) / 25);
    var g = Math.floor((b - f + 1) / 3);
    var h = (19 * a + b - d - g + 15) % 30;
    var i = Math.floor(c / 4);
    var k = c % 4;
    var l = (32 + 2 * e + 2 * i - h - k) % 7;
    var m = Math.floor((a + 11 * h + 22 * l) / 451);
    var month = Math.floor((h + l - 7 * m + 114) / 31);
    var dayOfMonth = (h + l - 7 * m + 114) % 31 + 1;
    return year + '-' + pad2(month) + '-' + pad2(dayOfMonth);
  }

  /** The state holiday's name on `day`, or null. */
  function holiday(day) {
    var fixed = STATE_HOLIDAYS[day.slice(5)];
    if (fixed) return fixed;
    return EASTER_HOLIDAYS[String(between(easter(+day.slice(0, 4)), day))] || null;
  }

  /** The clock changes of `year` (EU rule): [last Sunday of March, last Sunday of October]. */
  function clockChanges(year) {
    return [2, 9].map(function (month) {
      var last = new Date(Date.UTC(year, month + 1, 0));
      return format(Date.UTC(year, month, last.getUTCDate() - last.getUTCDay()));
    });
  }

  // --- moon (Meeus, Astronomical Algorithms, ch. 49: mean phase + the three largest periodic terms) ---

  var SYNODIC = 29.530588861;
  var JDE_K0 = 2451550.09766; // the new moon of k = 0 (6 January 2000)
  var UNIX_JD = 2440587.5;
  var RAD = Math.PI / 180;

  /**
   * The instant (epoch ms) of the phase `k` (an integer = new moon, + 0.5 = full moon); the sin M term times the
   * eccentricity factor E, TT taken as UT (the same in every app, so a phase near Prague midnight lands alike).
   */
  function phaseMs(k) {
    var t = k / 1236.85;
    var jde = JDE_K0 + SYNODIC * k + 0.00015437 * t * t - 0.00000015 * t * t * t + 0.00000000073 * t * t * t * t;
    var m = (2.5534 + 29.1053567 * k - 0.0000014 * t * t - 0.00000011 * t * t * t) * RAD;
    var mp = (201.5643 + 385.81693528 * k + 0.0107582 * t * t + 0.00001238 * t * t * t -
      0.000000058 * t * t * t * t) * RAD;
    var e = 1 - 0.002516 * t - 0.0000074 * t * t;
    var full = k - Math.floor(k) !== 0;
    jde += full
      ? -0.40614 * Math.sin(mp) + 0.17302 * e * Math.sin(m) + 0.01614 * Math.sin(2 * mp)
      : -0.4072 * Math.sin(mp) + 0.17241 * e * Math.sin(m) + 0.01608 * Math.sin(2 * mp);
    return (jde - UNIX_JD) * DAY;
  }

  /** 'full' | 'new' when that phase falls on the Prague day `day`, else null. */
  function moon(day) {
    var k0 = Math.floor(((parse(day) / DAY + UNIX_JD) - JDE_K0) / SYNODIC);
    for (var k = k0 - 1; k <= k0 + 1; k++) {
      if (CT.Days.today(phaseMs(k)) === day) return 'new';
      if (CT.Days.today(phaseMs(k + 0.5)) === day) return 'full';
    }
    return null;
  }

  /** Every new and full moon of `year`: [{date, phase}] in order (the golden list "moon2026"). */
  function moonPhases(year) {
    var out = [];
    var k0 = Math.floor(((Date.UTC(year, 0, 1) / DAY + UNIX_JD) - JDE_K0) / SYNODIC) - 1;
    for (var k = k0; k < k0 + 15; k += 0.5) {
      var date = CT.Days.today(phaseMs(k));
      if (+date.slice(0, 4) === year) out.push({ date: date, phase: k - Math.floor(k) ? 'full' : 'new' });
    }
    return out;
  }

  // --- the header ---

  /** `Svátek má Karel` / `Svátek mají A, B a C` / the holiday line / null. */
  function nameLine(day) {
    var name = holiday(day);
    if (name) return 'Dnes je státní svátek: ' + name;
    var names = NAMEDAYS[day.slice(5)];
    if (!names || !names.length) return null;
    if (names.length === 1) return 'Svátek má ' + names[0];
    return 'Svátek mají ' + names.slice(0, -1).join(', ') + ' a ' + names[names.length - 1];
  }

  function clockLine(day) {
    var year = +day.slice(0, 4);
    var lines = clockChanges(year).map(function (change, i) {
      var n = between(day, change);
      if (n < 0 || n > AHEAD_DAYS) return null;
      var when = n === 0 ? 'Dnes' : n === 1 ? 'Zítra' : 'V neděli ' + dayMonth(change);
      return when + ' se mění čas: ' + (i === 0 ? 'o hodinu dopředu' : 'o hodinu zpět');
    }).filter(Boolean);
    return lines[0] || null;
  }

  /** `29.–30. října`, `23. prosince – 3. ledna`, `29. ledna` */
  function range(from, to) {
    if (from === to) return dayMonth(from);
    if (from.slice(0, 7) === to.slice(0, 7)) return +from.slice(8) + '.–' + dayMonth(to);
    return dayMonth(from) + ' – ' + dayMonth(to);
  }

  function schoolLine(day) {
    for (var i = 0; i < SCHOOL_HOLIDAYS.length; i++) {
      var h = SCHOOL_HOLIDAYS[i];
      if (h.from <= day && day <= h.to) return 'Dnes jsou ' + lowerFirst(h.name);
      var n = between(day, h.from);
      if (n >= 1 && n <= AHEAD_DAYS) return h.name + ' ' + range(h.from, h.to);
    }
    return null;
  }

  /** Domů's header for the Prague day `day`: {fullDate, nameLine, lines}. */
  function header(day) {
    var d = new Date(parse(day));
    var tomorrow = holiday(format(parse(day) + DAY));
    var phase = moon(day);
    var lines = [
      tomorrow ? 'Zítra je státní svátek: ' + tomorrow : null,
      clockLine(day),
      schoolLine(day),
      phase === 'full' ? 'Dnes je úplněk' : phase === 'new' ? 'Dnes je nov' : null
    ].filter(Boolean).slice(0, MAX_LINES);
    return {
      fullDate: WEEKDAYS[(d.getUTCDay() + 6) % 7] + ' ' + dayMonth(day),
      nameLine: nameLine(day),
      lines: lines
    };
  }

  CT.Calendar = {
    WEEKDAYS: WEEKDAYS,
    MONTHS: MONTHS,
    STATE_HOLIDAYS: STATE_HOLIDAYS,
    EASTER_HOLIDAYS: EASTER_HOLIDAYS,
    SCHOOL_HOLIDAYS: SCHOOL_HOLIDAYS,
    NAMEDAYS: NAMEDAYS,
    easter: easter,
    holiday: holiday,
    clockChanges: clockChanges,
    moon: moon,
    phaseMs: phaseMs,
    moonPhases: moonPhases,
    header: header,
    /** The header of today in Prague at the moment `now`. */
    today: function (now) {
      return header(CT.Days.today(now));
    }
  };
})(typeof window !== 'undefined' ? window : global);
